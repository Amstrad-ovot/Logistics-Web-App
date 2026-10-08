import io
import re
import pandas as pd
import streamlit as st
from datetime import date, timedelta
from login import render_login_page
from src.sidebar import render_sidebar
from dashboard import render_purchase_dashboard, render_sales_dashboard
from main import (
    process_and_upload_excel,
    upload_customised_report,
    missing_updates,
    connect_gsheet,
    gsheet_call,
    upload_inward_data_excel,
)

# Fetch database sheet identifiers from Streamlit secrets
sales_db = st.secrets["connections"]["gsheets"]["sales_sheet"]
custom_db = st.secrets["connections"]["gsheets"]["custom_sheet"]
purchase_db = st.secrets["connections"]["gsheets"]["purchase_sheet"]


# Helper: Normalize text for flexible partial matching
def _clean_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


# Helper: Fetch unique locations for a specific sheet, filtered by user permissions
@st.cache_data(ttl=300, show_spinner=False)
def get_available_locations(sheet_name, user_role="", raw_user_locations=None):
    try:
        spreadsheet = connect_gsheet()
        worksheet = gsheet_call(lambda: spreadsheet.worksheet(sheet_name))
        
        # Optimize by fetching header first to find location column index
        headers = [str(h).strip().lower() for h in gsheet_call(lambda: worksheet.row_values(1))]
        
        if "location_name" in headers:
            col_idx = headers.index("location_name") + 1
            raw_locations = gsheet_call(lambda: worksheet.col_values(col_idx))[1:]  # Exclude header
            all_locations = sorted(list(set(str(loc).strip() for loc in raw_locations if str(loc).strip())))

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
                user_tokens = [_clean_token(loc) for loc in raw_list if _clean_token(loc)]

                for db_loc in all_locations:
                    db_token = _clean_token(db_loc)
                    if any(token in db_token or db_token in token for token in user_tokens):
                        matched_locations.append(db_loc)

                return matched_locations

            return []
    except Exception as e:
        print(f"Error fetching locations for sheet '{sheet_name}': {e}")
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
                help="Upload pre-processed sales figures or invoice summaries.",
            )

    if st.button("🚀 Process & Sync Custom Data", type="primary", use_container_width=False):
        if not custom_sales_file:
            st.warning("⚠️ Please select a customized sales file before proceeding.")
        else:
            with st.spinner("Processing customized sales data & updating database..."):
                success = upload_customised_report(custom_sales_file, custom_db.strip())
                if success:
                    st.cache_data.clear()
                    st.session_state.pop("missing_report_df", None)
                    st.success("✅ Customized sales report uploaded and synced successfully!")

    st.divider()

    st.header("📤 Upload Sales Report")

    with st.container():
        col1, _ = st.columns([2, 1])
        with col1:
            uploaded_file = st.file_uploader(
                "Select Excel Report File",
                type=["xlsx", "xls"],
                help="Ensure the file contains an 'invoice_doc_date' column.",
            )

    if st.button("🚀 Process & Upload", type="primary", use_container_width=False):
        if not uploaded_file:
            st.warning("⚠️ Please select an Excel file before proceeding.")
        else:
            with st.spinner("Processing file & updating database..."):
                success = process_and_upload_excel(uploaded_file, sales_db.strip(), custom_db)
                if success:
                    st.cache_data.clear()
                    st.session_state.pop("missing_report_df", None)
                    st.success("✅ Sales report uploaded successfully!")

    st.divider()

    st.header("📤 Upload Purchase/SRT Report")

    with st.container():
        col1, _ = st.columns([2, 1])
        with col1:
            uploaded_purchase_file = st.file_uploader(
                "Select Excel Report File",
                type=["xlsx", "xls"],
            )

    if st.button("🚀 Upload Inward Report", type="primary", use_container_width=False):
        if not uploaded_purchase_file:
            st.warning("⚠️ Please select an Excel file before proceeding.")
        else:
            with st.spinner("Processing file & updating database..."):
                success = upload_inward_data_excel(uploaded_purchase_file, purchase_db.strip())
                if success:
                    st.cache_data.clear()
                    st.session_state.pop("missing_report_df", None)
                    st.success("✅ Inward report uploaded successfully!")


# ─────────────────────────────────────────────────────────
# PAGE 2: Dashboard
# ─────────────────────────────────────────────────────────
elif page == "dashboard":
    st.title("📌 Logistics Dashboard")

    # ── DASHBOARD TYPE SELECTION (Sales vs. Inward/Purchase) ──
    dash_col, _ = st.columns([2, 2])
    with dash_col:
        dashboard_type = st.radio(
            "Select Dashboard View:",
            options=["Sales Dashboard", "Inward / Purchase Dashboard"],
            index=0,  # Sales Dashboard selected by default
            horizontal=True,
            key="selected_dashboard_type",
        )

    # Determine database target sheet, mode identifier, and key column based on selection
    if dashboard_type == "Sales Dashboard":
        selected_db = sales_db.strip()
        doc_id_col = "tax_invoice_no"
        dash_mode = "sales"
    else:
        selected_db = purchase_db.strip()
        doc_id_col = "grn_no"  # Purchase/Inward identifier column
        dash_mode = "purchase"


    # Clear prior missing data report if user switches dashboard views
    if st.session_state.get("active_dash_type") != dashboard_type:
        st.session_state["active_dash_type"] = dashboard_type
        st.session_state.pop("missing_report_df", None)

    st.markdown("---")

    # ── EXTRACT MISSING UPDATED DATA SECTION ─────────────────────────
    with st.expander(f"⚠️ Extract Missing Updated Data ({dashboard_type})", expanded=False):
        st.caption("Find records created at least 3 days ago where editable fields remain unupdated.")

        # Input Columns
        d1, d2, d3, d4 = st.columns([1.5, 1.5, 3, 2])

        with d1:
            from_date = st.date_input("From Date", value=None, key=f"missing_from_date_{dash_mode}")

        with d2:
            to_date = st.date_input("To Date", value=None, key=f"missing_to_date_{dash_mode}")

        # Fetch allowed locations for the selected target sheet
        available_locs = get_available_locations(
            sheet_name=selected_db,
            user_role=role,
            raw_user_locations=user_locs,
        )

        with d3:
            if is_admin:
                filter_locations = st.multiselect(
                    "Select Location(s)",
                    options=available_locs,
                    default=[],
                    placeholder="All Regions / Overall Company Data",
                    key=f"missing_locs_select_{dash_mode}",
                )
            else:
                filter_locations = st.multiselect(
                    "Your Assigned Region(s)",
                    options=available_locs,
                    default=available_locs,
                    placeholder="Assigned Regions Only",
                    key=f"missing_locs_select_{dash_mode}",
                )

        with d4:
            st.write(" ")
            st.write(" ")
            run_extract = st.button("🔍 Extract Missing Data", type="primary", use_container_width=True, key=f"btn_extract_{dash_mode}")

        if run_extract:
            with st.spinner("Searching for pending updates..."):
                query_locations = filter_locations if (filter_locations or is_admin) else available_locs

                missing_df = missing_updates(
                    from_date=from_date,
                    to_date=to_date,
                    filter_locations=query_locations,
                    target_sheet_name=selected_db,
                )
                st.session_state["missing_report_df"] = missing_df

        # Render Report Summary & Download Button
        if "missing_report_df" in st.session_state:
            df_result = st.session_state["missing_report_df"]

            if not df_result.empty:
                st.subheader(f"⚠️ Pending Records Found: {len(df_result)}")
                dl_col2, _ = st.columns([1.5, 5])

                # Determine dynamic column name for aggregation
                count_col = doc_id_col if doc_id_col in df_result.columns else df_result.columns[0]

                # Location Summary Table
                if "location_name" in df_result.columns:
                    summary = df_result.groupby("location_name", as_index=False).agg(
                        Unupdated_Count=(count_col, "count")
                    )
                else:
                    summary = pd.DataFrame({"Total Unupdated": [len(df_result)]})

                # Export to Excel
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                    summary.to_excel(writer, index=False, sheet_name="Summary")
                    df_result.to_excel(writer, index=False, sheet_name="Missing Updates Data")
                excel_data = buffer.getvalue()

                dl_col2.download_button(
                    label="📥 Download Excel",
                    data=excel_data,
                    file_name=f"missing_updates_{dash_mode}_{date.today().strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key=f"dl_missing_btn_{dash_mode}"
                )
            else:
                st.info("No unupdated records match your selected filter criteria.")

    st.markdown("---")

    # ── RENDER SPECIFIC DASHBOARD VIEW ─────────────────────────────────
    if dash_mode == "purchase":
        render_purchase_dashboard(purchase_db)
    else:
        render_sales_dashboard(sales_db)
