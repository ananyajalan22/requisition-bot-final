from flask import Flask, request, jsonify
import os
import json
import psycopg2
from dotenv import load_dotenv

# --- Configuration ---
load_dotenv()

# --- Vercel Postgres Database Setup ---
DATABASE_URL = os.getenv("POSTGRES_URL")

def get_db_connection():
    """Establishes a new database connection."""
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """Creates tables if they don't exist."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Create requisitions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS requisitions (
                id SERIAL PRIMARY KEY,
                requester_info TEXT,
                item_details TEXT,
                business_justification TEXT,
                required_by_date TEXT,
                approver TEXT,
                supplier_name TEXT,
                supplier_address TEXT,
                supplier_contact TEXT
            );
        """)
        # Create supplier_details table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS supplier_details (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                address TEXT,
                contact_number TEXT,
                is_blacklisted BOOLEAN DEFAULT FALSE
            );
        """)
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Database initialization failed: {e}")

# Run DB initialization when the function starts
init_db()

# --- State Management and Constants ---
user_state = {}
FORM_FIELDS = ["Requester Information", "Item/Service Details", "Business Justification", "Required By Date", "Supplier Preference", "Approval Section"]
SUPPLIER_CHECK_STATE = "AWAITING_SUPPLIER_CHOICE"

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Helper Functions ---
def get_user_session_id():
    # Using a combination of IP and User-Agent for a slightly more unique session ID
    return request.remote_addr + request.headers.get('User-Agent', '')

def reset_form_state(session_id):
    user_state[session_id] = {
        "current_field_index": 0,
        "form_data": {},
        "special_state": None
    }

# --- Main API Endpoint ---
@app.route('/chat', methods=['POST'])
def chat():
    try:
        session_id = get_user_session_id()
        data = request.get_json()
        if not data or 'message' not in data:
            return jsonify({"error": "Request must be JSON with a 'message' key"}), 400
        
        user_input = data['message'].strip()

        if session_id not in user_state or user_input.lower() in ['start', 'restart', 'edit']:
            reset_form_state(session_id)
            return jsonify({'reply': f"Hello! Let's fill out a requisition form. Please provide the **{FORM_FIELDS[0]}**."})

        current_state = user_state[session_id]

        # --- STATE-BASED LOGIC ---
        if current_state.get("special_state") == SUPPLIER_CHECK_STATE:
            return handle_supplier_reselection(session_id, user_input)

        if current_state["current_field_index"] >= len(FORM_FIELDS):
            return handle_form_confirmation(session_id, user_input)

        return process_current_field(session_id, user_input)

    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return jsonify({'reply': f'An unexpected server error occurred. Please try again later.'}), 500

def process_current_field(session_id, user_input):
    current_state = user_state[session_id]
    current_field_name = FORM_FIELDS[current_state["current_field_index"]]

    if current_field_name == "Supplier Preference":
        return handle_supplier_preference(session_id, user_input)
    else:
        current_state["form_data"][current_field_name] = user_input
        current_state["current_field_index"] += 1
        return ask_next_question(session_id)

def handle_supplier_preference(session_id, user_input):
    current_state = user_state[session_id]
    if user_input.lower() in ['skip', 'none', 'no preference', 'blank']:
        current_state["form_data"]["Supplier Preference"] = "N/A"
        current_state["current_field_index"] += 1
        return ask_next_question(session_id)
    
    status, supplier_data = check_supplier_db(user_input)
    if status == "OK":
        populate_supplier_data(current_state, supplier_data)
        current_state["current_field_index"] += 1
        return ask_next_question(session_id)
    else:
        return handle_bad_supplier(session_id, status, user_input)

def handle_supplier_reselection(session_id, user_input):
    current_state = user_state[session_id]
    if user_input.lower() in ['skip', 'none', 'no preference', 'blank']:
        current_state["form_data"]["Supplier Preference"] = "N/A"
        current_state["special_state"] = None
        current_state["current_field_index"] += 1
        return ask_next_question(session_id)

    status, supplier_data = check_supplier_db(user_input)
    if status == "OK":
        populate_supplier_data(current_state, supplier_data)
        current_state["special_state"] = None
        current_state["current_field_index"] += 1
        return ask_next_question(session_id)
    else:
        return handle_bad_supplier(session_id, status, user_input)

def handle_form_confirmation(session_id, user_input):
    if user_input.lower() == 'save':
        return save_form(session_id)
    else:
        reset_form_state(session_id)
        return jsonify({'reply': f"Okay, let's start over. Please provide the **{FORM_FIELDS[0]}**."})

def ask_next_question(session_id):
    current_state = user_state[session_id]
    field_index = current_state["current_field_index"]
    
    if field_index < len(FORM_FIELDS):
        next_field_name = FORM_FIELDS[field_index]
        reply_text = f"Got it. Now, please provide the **{next_field_name}**."
        if next_field_name == "Supplier Preference":
            reply_text = f"Please provide your **{next_field_name}**. (You can type 'skip' if you don't have one)."
        return jsonify({'reply': reply_text})
    else:
        form_preview = json.dumps(current_state['form_data'], indent=2)
        reply_text = (f"Great, all information is collected!\n\nSummary:\n```json\n{form_preview}\n```\n\nWould you like to **save** or **edit**?")
        return jsonify({'reply': reply_text})

def check_supplier_db(supplier_name):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name, address, contact_number, is_blacklisted FROM supplier_details WHERE name ILIKE %s", (supplier_name,))
    supplier = cursor.fetchone()
    cursor.close()
    conn.close()
    if not supplier: return "NOT_FOUND", None
    if supplier[3]: return "BLACKLISTED", supplier[0]
    return "OK", supplier

def populate_supplier_data(current_state, supplier_data):
    s_name, s_address, s_contact, _ = supplier_data
    current_state["form_data"]["Supplier Preference"] = s_name
    current_state["form_data"]["Supplier Name"] = s_name
    current_state["form_data"]["Supplier Address"] = s_address
    current_state["form_data"]["Supplier Contact"] = s_contact

def handle_bad_supplier(session_id, status, supplier_name):
    user_state[session_id]["special_state"] = SUPPLIER_CHECK_STATE
    message = f"The supplier '{supplier_name}' was not found."
    if status == "BLACKLISTED":
        message = f"**Warning:** The supplier '{supplier_name}' is on our blacklist."
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM supplier_details WHERE is_blacklisted = FALSE ORDER BY name;")
    approved_suppliers = "\n- ".join([row[0] for row in cursor.fetchall()])
    cursor.close()
    conn.close()
    
    reply_text = f"{message}\nPlease choose an approved supplier, or type 'skip':\n- {approved_suppliers}"
    return jsonify({'reply': reply_text})

def save_form(session_id):
    form_data = user_state[session_id]['form_data']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO requisitions (requester_info, item_details, business_justification, required_by_date, approver, supplier_name, supplier_address, supplier_contact)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        form_data.get("Requester Information"), form_data.get("Item/Service Details"),
        form_data.get("Business Justification"), form_data.get("Required By Date"),
        form_data.get("Approval Section"), form_data.get("Supplier Name"),
        form_data.get("Supplier Address"), form_data.get("Supplier Contact")
    ))
    conn.commit()
    cursor.close()
    conn.close()
    reset_form_state(session_id)
    return jsonify({'reply': "Form saved! You can start a new one by sending 'start'."})

@app.route('/forms', methods=['GET'])
def get_forms():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM requisitions ORDER BY id DESC")
        columns = [desc[0] for desc in cursor.description]
        forms = [dict(zip(columns, row)) for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        return jsonify(forms)
    except Exception as e:
        return jsonify({'error': f'Could not retrieve forms: {str(e)}'}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def catch_all(path):
    return jsonify({"message": "API is running. Use /chat or /forms endpoints."})