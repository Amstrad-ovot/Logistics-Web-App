import io
import re
import pandas as pd
import streamlit as st
from datetime import date, timedelta
from login import render_login_page
from src.sidebar import render_sidebar
from dashboard import render_dashboard_page
from main import process_and_upload_excel, upload_customised_report, missing_updates, connect_gsheet

sales_db = st.secrets["connections"]["gsheets"]["sales_sheet"]
custom_db = st.secrets["connections"]["gsheets"]["custom_sheet"]


# Helper: Normalize text for flexible partial matching
def _clean_token(text: str) -> str:
    return re.sub(r'[^a-z0-9]', '', str(text).lower())


# Helper: Fetch unique locations, filtered by user permission role with partial matching
@st.cache_data(ttl=300, show_spinner= False)
def get_available_locations(sheet_name, user_role="", raw_user_locations=None):
    try:
        spreadsheet = connect_gsheet()
        worksheet = spreadsheet.worksheet(sheet_name)
        records = worksheet.get_all_records()
        if records:
            df = pd.DataFrame(records)
            df.columns = [str(c).strip().lower() for c in df.columns]
            
            if "location_name" in df.columns:
                all_locations = sorted(df["location_name"].dropna().astype(str).str.strip().unique().tolist())
                
                role_clean = str(user_role).strip().lower()

                # Admins get full access to all dataset locations
                if role_clean in ("admin", "super admin", "superadmin"):
                    return all_locations

                # Parse allowed regions from user session
                raw_list = []
                if isinstance(raw_user_locations, str):
                    raw_list = [loc.strip() for loc in raw_user_locations.split(",") if loc.strip()]
                elif isinstance(raw_user_locations, (list, tuple, set)):
                    raw_list = [str(loc).strip() for loc in raw_user_locations if str(loc).strip()]

                if raw_list:
                    matched_locations = []
                    
                    # Clean tokens for flexible comparison (e.g. "kerala", "ernakulam")
                    user_tokens = [_clean_token(loc) for loc in raw_list if _clean_token(loc)]

                    for db_loc in all_locations:
                        db_token = _clean_token(db_loc) # e.g. "aciplkerala" or "aciplernakulam"
                        
                        # Partial substring check: "kerala" in "aciplkerala" OR "aciplkerala" in "kerala"
                        if any(token in db_token or db_token in token for token in user_tokens):
                            matched_locations.append(db_loc)

                    # Return matched dataset locations, or fallback to raw list if none match
                    return matched_locations if matched_locations else raw_list

                return []
    except Exception as e:
        print(f"Error fetching locations: {e}")
    return []


st.set_page_config(page_title="Logistic Web App", page_icon="🚚", layout="wide")

# ── 1. Login Gate ─────────────────────────────────────────
if not render_login_page():
    st.stop()

# ── 2. Sidebar Navigation ────────────────────────────────
page = render_sidebar()

# ── 3. Session User Info ──────────────────────────────────
role = st.session_state.get("user_role", "").strip().lower()
is_admin = role in ("admin", "super admin", "superadmin")

# Extract regional permissions safely from session
user_locs = (
    st.session_state.get("user_regions")
    or st.session_state.get("user_locations")
    or st.session_state.get("location")
    or st.session_state.get("region", "")
)

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
    st.title("📌 Logistics Dashboard")
    # ── EXTRACT MISSING UPDATED DATA SECTION ─────────────────────────
    with st.expander("⚠️ Extract Missing Updated Data", expanded=False):
        st.caption("Find records created at least 3 days ago where editable fields remain unupdated.")

        # 4 Input Columns
        d1, d2, d3, d4 = st.columns([1.5, 1.5, 3, 2])

        with d1:
            from_date = st.date_input("From Date", value=None, key="missing_from_date")
        
        with d2:
            to_date = st.date_input("To Date", value=None, key="missing_to_date")

        # Fetch allowed locations based on role and fuzzy matching
        available_locs = get_available_locations(
            sheet_name=sales_db.strip(),
            user_role=role,
            raw_user_locations=user_locs
        )

        with d3:
            if is_admin:
                # ADMIN / SUPER ADMIN: Default is overall company data
                filter_locations = st.multiselect(
                    "Select Location(s)",
                    options=available_locs,
                    default=[],
                    placeholder="All Regions / Overall Company Data",
                    key="missing_locations_select"
                )
            else:
                # LOGISTICS USER: Restricted and defaulted strictly to matched assigned regions
                filter_locations = st.multiselect(
                    "Your Assigned Region(s)",
                    options=available_locs,
                    default=available_locs,
                    placeholder="Assigned Regions Only",
                    key="missing_locations_select"
                )

        with d4:
            st.write(" ")  # Spacing alignment
            st.write(" ")
            run_extract = st.button("🔍 Extract Missing Data", type="primary", use_container_width=True)

        if run_extract:
            with st.spinner("Searching for pending updates..."):
                if is_admin:
                    query_locations = filter_locations
                else:
                    query_locations = filter_locations if filter_locations else available_locs

                missing_df = missing_updates(
                    from_date=from_date,
                    to_date=to_date,
                    filter_locations=query_locations,
                    target_sheet_name=sales_db.strip()
                )
                st.session_state["missing_report_df"] = missing_df

        # Render Report Summary & Download Button
        if "missing_report_df" in st.session_state:
            df_result = st.session_state["missing_report_df"]

            if not df_result.empty:
                st.subheader(f"⚠️ Pending Records Found: {len(df_result)}")

                dl_col2, _ = st.columns([1.5, 5])

                # Location Summary Table
                summary = df_result.groupby("location_name", as_index=False).agg(
                    Unupdated_Count=("tax_invoice_no", "count")
                )

                # Export to Excel
                buffer = io.BytesIO()
                # with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
                #     summary.to_excel(writer, index=False, sheet_name="Summary")
                #     df_result.to_excel(writer, index=False, sheet_name="Missing Updates Data")
                with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                    summary.to_excel(writer, index=False, sheet_name="Summary")
                    df_result.to_excel(writer, index=False, sheet_name="Missing Updates Data")
                excel_data = buffer.getvalue()

                dl_col2.download_button(
                    label="📥 Download Excel",
                    data=excel_data,
                    file_name=f"missing_updates_{date.today().strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
            else:
                st.info("No unupdated records match your selected filter criteria.")

    st.markdown("---")

    # Render Main Dashboard Data Grid
    render_dashboard_page(sales_db)
