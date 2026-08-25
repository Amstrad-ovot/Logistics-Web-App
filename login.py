import streamlit as st
from main import connect_gsheet, show_popup


# ──────────────────────────────────────────────
# Data layer
# ──────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner= False)  # Caches result for 60 seconds to prevent hitting GSheet API limits
def fetch_users():
    """Fetches user records from the 'Users' tab in Google Sheets."""
    try:
        spreadsheet = connect_gsheet()
        users_ws = spreadsheet.worksheet("Users")
        return users_ws.get_all_records()
    except Exception as e:
        st.error(f"Could not fetch users: {e}")
        return []


# ──────────────────────────────────────────────
# Auth helpers
# ──────────────────────────────────────────────

def authenticate(mobile: str, password: str):
    """Matches provided mobile and password against the Google Sheet records."""
    users = fetch_users()
    clean_mobile = str(mobile).strip()
    clean_password = str(password).strip()

    for user in users:
        db_mobile = str(user.get("Mobile Number", "")).strip()
        db_password = str(user.get("Password", "")).strip()

        if db_mobile == clean_mobile and db_password == clean_password:
            return user
    return None


def logout():
    """Clear ALL session state so the login page renders fresh."""
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()


# ──────────────────────────────────────────────
# CSS Styles
# ──────────────────────────────────────────────

LOGIN_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght=300;400;600;700&display=swap');

html, body, [data-testid="stAppViewContainer"] {
    background: linear-gradient(135deg, #0d1b2a 0%, #16213e 60%, #1e3a5f 100%) !important;
    font-family: 'Outfit', sans-serif;
}

#MainMenu, footer, header { visibility: hidden; }

.login-title {
    color: #ffffff !important;
    font-size: 28px;
    font-weight: 700;
    text-align: center;
    letter-spacing: 0.3px;
    margin: 0 0 4px 0;
    padding: 0;
}

.login-subtitle {
    color: #7f9fbf !important;
    font-size: 13px;
    text-align: center;
    letter-spacing: 1.4px;
    text-transform: uppercase;
    margin: 0 0 16px 0;
    padding: 0;
}

[data-testid="stForm"] {
    background: rgba(255, 255, 255, 0.04) !important;
    border: 1px solid rgba(99, 179, 237, 0.18) !important;
    border-radius: 20px !important;
    padding: 32px 36px 36px !important;
    box-shadow: 0 8px 48px rgba(0, 0, 0, 0.55) !important;
    backdrop-filter: blur(12px) !important;
    -webkit-backdrop-filter: blur(12px) !important;
}

/* Base container for input fields */
[data-testid="stTextInput"] div[data-baseweb="input"] {
    background-color: rgba(15, 23, 42, 0.6) !important;
    border: 1px solid rgba(99, 179, 237, 0.3) !important;
    border-radius: 10px !important;
}

# /* Force white text for both plain text and password masking bullets */
# [data-testid="stTextInput"] input[type="text"],
# [data-testid="stTextInput"] input[type="password"] {
#     color: #ffffff !important;
#     font-family: 'Outfit', sans-serif !important;
#     font-size: 16px !important;
#     background-color: transparent !important;
#     -webkit-text-fill-color: #ffffff !important;
# }

/* FIX: Targets both text AND password input elements universally, 
   removing hardcoded text colors to allow dynamic system theme matching */
[data-testid="stTextInput"] input[type="text"],
[data-testid="stTextInput"] input[type="password"] {
    background: rgba(255, 255, 255, 0.07) !important;
    border: 1px solid rgba(99, 179, 237, 0.25) !important;
    border-radius: 10px !important;
    font-family: 'Outfit', sans-serif !important;
    font-size: 17px !important;
    padding: 14px 16px !important;
    transition: border-color 0.2s ease !important;
}

/* Target placeholder text specifically */
[data-testid="stTextInput"] input::placeholder {
    color: #64748b !important;
    -webkit-text-fill-color: #64748b !important;
    opacity: 1 !important;
}

/* Style the eye toggle icon inside password field */
[data-testid="stTextInput"] div[data-baseweb="input"] svg,
[data-testid="stTextInput"] div[data-baseweb="input"] button {
    fill: #a0b8d0 !important;
    color: #a0b8d0 !important;
}

/* Autofill rules for Chrome/Edge/Safari */
[data-testid="stTextInput"] input:-webkit-autofill,
[data-testid="stTextInput"] input:-webkit-autofill:hover, 
[data-testid="stTextInput"] input:-webkit-autofill:focus, 
[data-testid="stTextInput"] input:-webkit-autofill:active {
    -webkit-box-shadow: 0 0 0 1000px #16213e inset !important;
    -webkit-text-fill-color: #ffffff !important;
    caret-color: #ffffff !important;
    transition: background-color 5000s ease-in-out 0s;
}

/* Focus state */
[data-testid="stTextInput"] div[data-baseweb="input"]:focus-within {
    border-color: #63b3ed !important;
    box-shadow: 0 0 0 3px rgba(99, 179, 237, 0.2) !important;
}

/* Labels */
[data-testid="stTextInput"] label {
    color: #a0b8d0 !important;
    font-size: 14px !important;
    font-weight: 600 !important;
    letter-spacing: 0.8px !important;
    text-transform: uppercase !important;
}

[data-testid="stForm"] .stButton > button {
    width: 100% !important;
    background: linear-gradient(135deg, #2b6cb0 0%, #2c5282 100%) !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 10px !important;
    padding: 15px 0 !important;
    font-size: 18px !important;
    font-weight: 600 !important;
    font-family: 'Outfit', sans-serif !important;
    letter-spacing: 0.5px !important;
    cursor: pointer !important;
    transition: all 0.2s ease !important;
    margin-top: 8px !important;
}

[data-testid="stForm"] .stButton > button:hover {
    background: linear-gradient(135deg, #3182ce 0%, #2b6cb0 100%) !important;
    box-shadow: 0 4px 20px rgba(49,130,206,0.4) !important;
    transform: translateY(-1px) !important;
}

[data-testid="stAlert"] {
    border-radius: 10px !important;
    font-family: 'Outfit', sans-serif !important;
}

[data-testid="stSpinner"] {
    color: #ffffff !important;
}
[data-testid="stSpinner"] p {
    color: #e2e8f0 !important;
}
</style>
"""


# ──────────────────────────────────────────────
# Render
# ──────────────────────────────────────────────

def render_login_page() -> bool:
    """Renders the login UI. Returns True if authenticated, False otherwise."""
    if st.session_state.get("authenticated"):
        return True

    st.markdown(LOGIN_CSS, unsafe_allow_html=True)

    _, col, _ = st.columns([1, 2, 1])

    with col:
        st.markdown(
            '<p class="login-title">🚚 AMSTRAD - Logistic Web App</p>'
            '<p class="login-subtitle">Sign in to continue</p>',
            unsafe_allow_html=True,)

        with st.form("login_form", clear_on_submit=False):
            mobile = st.text_input("📱 Mobile Number", placeholder="Enter your mobile number")
            password = st.text_input("🔒 Password", type="password", placeholder="Enter your password")
            submitted = st.form_submit_button("Sign In →", use_container_width=True)

            if submitted:
                if not mobile.strip() or not password.strip():
                    st.warning("Please enter both mobile number and password.")
                else:
                    with st.spinner("Verifying credentials…"):
                        user = authenticate(mobile, password)

                    if user:
                        st.session_state["authenticated"] = True
                        st.session_state["user_name"] = str(user.get("Name", "User")).strip()
                        st.session_state["user_role"] = str(user.get("Role", "")).strip()
                        st.session_state["user_regions"] = str(user.get("Regions", "")).strip()
                        st.session_state["user_mobile"] = mobile.strip()
                        st.session_state["user_code"] = str(user.get("Code", "")).strip()
                        st.toast(f"✅ Welcome, {st.session_state['user_name']}!")
                        st.rerun()
                    else:
                        st.error("❌ Invalid mobile number or password. Please try again.")

    return st.session_state.get("authenticated", False)
