from flask import Flask, request, jsonify
import os
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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS requisitions (
                id SERIAL PRIMARY KEY,
                requester_info TEXT, item_details TEXT,
                business_justification TEXT, required_by_date TEXT,
                approver TEXT, supplier_name TEXT,
                supplier_address TEXT, supplier_contact TEXT
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS supplier_details (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE, address TEXT,
                contact_number TEXT, is_blacklisted BOOLEAN DEFAULT FALSE
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

        if current_state.get("special_state") == SUPPLIER_CHECK_STATE:
            return handle_supplier_reselection(session_id, user_input)

        if current_state["current_field_index"] >= len(FORM_FIELDS):
            return handle_form_confirmation(session_id, user_input)

        return process_current_field(session_id, user_input)

    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return jsonify({'reply': 'An unexpected server error occurred. Please try again later.'}), 500

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
    
    status, data = check_supplier_db(user_input)
    if status == "OK":
        populate_supplier_data(current_state, data)
        current_state["current_field_index"] += 1
        return ask_next_question(session_id)
    else:
        return handle_bad_supplier(session_id, status, data)

def handle_supplier_reselection(session_id, user_input):
    current_state = user_state[session_id]
    if user_input.lower() in ['skip', 'none', 'no preference', 'blank']:
        current_state["form_data"]["Supplier Preference"] = "N/A"
        current_state["special_state"] = None
        current_state["current_field_index"] += 1
        return ask_next_question(session_id)

    status, data = check_supplier_db(user_input)
    if status == "OK":
        populate_supplier_data(current_state, data)
        current_state["special_state"] = None
        current_state["current_field_index"] += 1
        return ask_next_question(session_id)
    else:
        return handle_bad_supplier(session_id, status, data)

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
        reply_text = f"Great, all information is collected!\n\nSummary:\n```json\n{form_preview}\n```\n\nWould you like to **save** or **edit**?"
        return jsonify({'reply': reply_text})

def check_supplier_db(supplier_name):
    conn = get_db_connection()
    cursor = conn.cursor()
    # --- THE FIX: Added a comma inside the tuple to correctly pass the parameter ---
    cursor.execute("SELECT name, address, contact_number, is_blacklisted FROM supplier_details WHERE name ILIKE %s", (supplier_name,))
    supplier = cursor.fetchone()
    cursor.close()
    conn.close()
    if not supplier: return "NOT_FOUND", supplier_name
    if supplier[3]: return "BLACKLISTED", supplier[0]
    return "OK", supplier

def populate_supplier_data(current_state, supplier_data):
    s_name, s_address, s_contact, _ = supplier_data