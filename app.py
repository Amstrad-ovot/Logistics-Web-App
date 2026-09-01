import streamlit as st
from login import render_login_page
from src.sidebar import render_sidebar
from main import process_and_upload_excel, upload_customised_report
from dashboard import render_dashboard_page


sales_db = st.secrets["connections"]["gsheets"]["sales_sheet"]
custom_db = st.secrets["connections"]["gsheets"]["custom_sheet"]

st.set_page_config(page_title="Logistic Web App", page_icon="🚚", layout="wide")

# ── 1. Login Gate ─────────────────────────────────────────
if not render_login_page():
    st.stop()

# ── 2. Sidebar Navigation ────────────────────────────────
page = render_sidebar()

# ── 3. Session User Info ──────────────────────────────────
role = st.session_state.get("user_role", "").strip().lower()
is_admin = role in ("admin", "super admin", "superadmin")

# ─────────────────────────────────────────────────────────
# PAGE 1: Upload Data (Admin / Super Admin Only)
# ─────────────────────────────────────────────────────────
if page == "upload":
    if not is_admin:
        st.error("🔒 Access Denied: You do not have permission to view this page.")
        st.stop()

    st.header("📊 Upload Customized Sales Report")

    with st.container():
        col1, _ = st.columns([2, 1])

        with col1:
            custom_sales_file = st.file_uploader(
                "Select Customized Sales Report File",
                type=["xlsx", "xls"],
                key="custom_sales_uploader",
                help="Upload pre-processed sales figures or invoice summaries."
            )

    if st.button("🚀 Process & Sync Custom Data", type="primary", use_container_width=False):
        if not custom_sales_file:
            st.warning("⚠️ Please select a customized sales file before proceeding.")
        else:
            with st.spinner("Processing customized sales data & updating database..."):
                # Call custom upload processing function from main.py or dedicated module
                success = upload_customised_report(custom_sales_file, custom_db.strip())
                if success:
                    st.cache_data.clear()
                    st.success("✅ Customized sales report uploaded and synced successfully!")

    st.divider()

    st.header("📤 Upload Sales Report")

    with st.container():
        col1, _ = st.columns([2, 1])

        with col1:
            uploaded_file = st.file_uploader(
                "Select Excel Report File",
                type=["xlsx", "xls"],
                help="Ensure the file contains an 'invoice_doc_date' column."
            )

    if st.button("🚀 Process & Upload", type="primary", use_container_width=False):
        if not uploaded_file:
            st.warning("⚠️ Please select an Excel file before proceeding.")
        else:
            with st.spinner("Processing file & updating database..."):
                success = process_and_upload_excel(uploaded_file, sales_db.strip(), custom_db)
                if success:
                    st.cache_data.clear()  # Clear cache so dashboard displays updated data immediately

# ─────────────────────────────────────────────────────────
# PAGE 2: Dashboard
# ─────────────────────────────────────────────────────────
elif page == "dashboard":
    render_dashboard_page(sales_db)
