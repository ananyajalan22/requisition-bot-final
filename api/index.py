from flask import Flask, request, jsonify
import os
import psycopg2
from dotenv import load_dotenv
import json

# --- Configuration ---
load_dotenv()

# --- Vercel Postgres Database Setup ---
DATABASE_URL = os.getenv("POSTGRES_URL")
# --- NEW: Definitive check to ensure the environment variable is loaded ---
if not DATABASE_URL:
    # This will cause the deployment to fail with a clear log message
    # if the POSTGRES_URL is not set on Vercel, preventing 500 errors.
    raise RuntimeError("FATAL: POSTGRES_URL environment variable is not set.")

def get_db_connection():
    """Establishes a new database connection."""
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """Creates tables if they don't exist."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS requisitions (
                        id SERIAL PRIMARY KEY, requester_info TEXT, item_details TEXT,
                        business_justification TEXT, required_by_date TEXT,
                        approver TEXT, supplier_name TEXT,
                        supplier_address TEXT, supplier_contact TEXT
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS supplier_details (
                        id SERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE, address TEXT,
                        contact_number TEXT, is_blacklisted BOOLEAN DEFAULT FALSE
                    );
                """)
    except Exception as e:
        print(f"Database initialization failed: {e}")

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
    user_state[session_id] = {"current_field_index": 0, "form_data": {}, "special_state": None}

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
        print(f"An unexpected error occurred in chat(): {e}")
        return jsonify({'reply': 'An unexpected server error occurred. Please check the logs.'}), 500

def process_current_field(session_id, user_input):
    current_state = user_state[session_id]
    current_field_name = FORM_FIELDS[current_state["current_field_index"]]

    if current_field_name == "Supplier Preference":
        return handle_supplier_preference(session_id, user_input)
    
    current_state["form_data"][current_field_name] = user_input
    current_state["current_field_index"] += 1
    return ask_next_question(session_id)

def handle_supplier_preference(session_id, user_input):
    if user_input.lower() in ['skip', 'none', 'no preference', 'blank']:
        user_state[session_id]["form_data"]["Supplier Preference"] = "N/A"
        user_state[session_id]["current_field_index"] += 1
        return ask_next_question(session_id)
    
    status, data = check_supplier_db(user_input)
    if status == "OK":
        populate_supplier_data(user_state[session_id], data)
        user_state[session_id]["current_field_index"] += 1
        return ask_next_question(session_id)
    elif status == "DB_ERROR":
        return jsonify({'reply': f"Database Error: {data}"}), 500
    else: # BLACKLISTED or NOT_FOUND
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
    elif status == "DB_ERROR":
        return jsonify({'reply': f"Database Error: {data}"}), 500
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
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT name, address, contact_number, is_blacklisted FROM supplier_details WHERE name ILIKE %s", (supplier_name,))
                supplier = cursor.fetchone()
        
        if not supplier: return "NOT_FOUND", supplier_name
        if supplier[3]: return "BLACKLISTED", supplier[0]
        return "OK", supplier
    except Exception as e:
        print(f"DATABASE ERROR in check_supplier_db: {e}")
        return "DB_ERROR", str(e)

def populate_supplier_data(current_state, supplier_data):
    s_name, s_address, s_contact, _ = supplier_data
    current_state["form_data"]["Supplier Preference"] = s_name
    current_state["form_data"]["Supplier Name"] = s_name
    current_state["form_data"]["Supplier Address"] = s_address
    current_state["form_data"]["Supplier Contact"] = s_contact

def handle_bad_supplier(session_id, status, supplier_name_data):
    user_state[session_id]["special_state"] = SUPPLIER_CHECK_STATE
    message = f"The supplier '{supplier_name_data}' was not found."
    if status == "BLACKLISTED":
        message = f"**Warning:** The supplier '{supplier_name_data}' is on our blacklist."
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT name FROM supplier_details WHERE is_blacklisted = FALSE ORDER BY name;")
                approved_suppliers = "\n- ".join([row[0] for row in cursor.fetchall()])
        
        reply_text = f"{message}\nPlease choose an approved supplier, or type 'skip':\n- {approved_suppliers}"
        return jsonify({'reply': reply_text})
    except Exception as e:
        print(f"DATABASE ERROR in handle_bad_supplier: {e}")
        return jsonify({'reply': 'Error retrieving supplier list.'}), 500

def save_form(session_id):
    try:
        form_data = user_state[session_id]['form_data']
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO requisitions (requester_info, item_details, business_justification, required_by_date, approver, supplier_name, supplier_address, supplier_contact)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    form_data.get("Requester Information"), form_data.get("Item/Service Details"),
                    form_data.get("Business Justification"), form_data.get("Required By Date"),
                    form_data.get("Approval Section"), form_data.get("Supplier Name"),
                    form_data.get("Supplier Address"), form_data.get("Supplier Contact")
                ))
        reset_form_state(session_id)
        return jsonify({'reply': "Form saved! You can start a new one by sending 'start'."})
    except Exception as e:
        print(f"DATABASE ERROR in save_form: {e}")
        return jsonify({'reply': f'A database error occurred while saving the form.'}), 500

@app.route('/forms', methods=['GET'])
def get_forms():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM requisitions ORDER BY id DESC")
                columns = [desc[0] for desc in cursor.description]
                forms = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return jsonify(forms)
    except Exception as e:
        return jsonify({'error': f'Could not retrieve forms: {str(e)}'}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def catch_all(path):
    return jsonify({"message": "API is running. Use /chat or /forms endpoints."})