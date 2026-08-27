import io
import time
import threading
import numpy as np
import pandas as pd
import streamlit as st
from main import connect_gsheet, show_popup

# Global thread lock for synchronized GSheet updates across concurrent sessions
DB_LOCK = threading.Lock()

DEFAULT_COLUMNS = [
    "id", "location_name", "invoice_type_desc", "invoice_doc_date", "tax_invoice_no",
    "customer_name", "place_of_supply", "fg_qty", "selling_fc_value", "igst",
    "cgst", "sgst", "invoiced_value_fc", "vehicle_no", "eway_bill_no", "transporter_name", "challan_no",
    "vehicle_type", "approx_distance", "provisional_freight_amount",
    "provisional_perc", "actual_freight_amount", "actual_perc", "lr_charges", "loading_charges",
    "unloading_charges", "detension_charges", "point_charges", "total_freight_cost", 
    "cost_per_km", "po_no", "bill_no", "bill_date", "bill_receiving_status", "remark"
]

CONDITIONAL_COLS = {
    "actual_freight_amount",
    "unloading_charges",
    "detension_charges",
    "point_charges",
}


# ── Helper: Fetch Worksheet Data ─────────────────────────
@st.cache_data(ttl=30)
def load_worksheet_data(sheet_name: str) -> pd.DataFrame:
    spreadsheet = connect_gsheet()
    worksheet = spreadsheet.worksheet(sheet_name)
    data = worksheet.get_all_records()
    return pd.DataFrame(data)


def gspread_cell_format(row: int, col: int) -> str:
    col_str = ""
    while col > 0:
        col, remainder = divmod(col - 1, 26)
        col_str = chr(65 + remainder) + col_str
    return f"{col_str}{row}"


# ── Helper: Atomic & Concurrent-Safe Google Sheet Updates (Optimized) ─────
def update_gsheet_atomic(sheet_name: str, id_col: str, pending_edits: dict, required_columns: list = None) -> bool:
    if not pending_edits and not required_columns:
        return True

    with DB_LOCK:
        try:
            spreadsheet = connect_gsheet()
            worksheet = spreadsheet.worksheet(sheet_name)

            raw_headers = list(worksheet.row_values(1))
            headers_lookup = [str(h).strip().lower() for h in raw_headers]
            id_col_clean = id_col.strip().lower()

            new_cols_added = False
            target_cols = set(required_columns) if required_columns else set()
            
            for changes in pending_edits.values():
                target_cols.update(changes.keys())

            for col_name in target_cols:
                if col_name == "parsed_invoice_date":
                    continue
                clean_name = str(col_name).strip().lower()
                if clean_name not in headers_lookup:
                    raw_headers.append(str(col_name))
                    headers_lookup.append(clean_name)
                    new_cols_added = True

            if new_cols_added:
                worksheet.update("1:1", [raw_headers])

            if id_col_clean not in headers_lookup:
                st.error(f"Column '{id_col}' not found in Database headers.")
                return False

            id_col_idx = headers_lookup.index(id_col_clean) + 1
            
            # 🚀 OPTIMIZATION: Fetch entire ID column at once instead of calling find() N times
            column_ids = worksheet.col_values(id_col_idx)
            id_to_row_map = {str(val).strip(): idx + 1 for idx, val in enumerate(column_ids)}

            cells_to_update = []

            for record_id, changes in pending_edits.items():
                record_id_str = str(record_id).strip()
                target_row = id_to_row_map.get(record_id_str)
                
                if not target_row:
                    st.warning(f"⚠️ Row for ID #{record_id} not found in Google Sheet.")
                    continue

                for col_name, new_val in changes.items():
                    if col_name == "parsed_invoice_date":
                        continue

                    col_name_clean = str(col_name).strip().lower()
                    if col_name_clean in headers_lookup:
                        col_idx = headers_lookup.index(col_name_clean) + 1

                        if pd.isna(new_val) or new_val is None:
                            val_str = ""
                        elif isinstance(new_val, (pd.Timestamp, pd.DatetimeIndex)):
                            val_str = new_val.strftime("%Y-%m-%d")
                        else:
                            val_str = str(new_val)

                        cells_to_update.append({
                            "range": gspread_cell_format(target_row, col_idx),
                            "values": [[val_str]]
                        })

            if cells_to_update:
                worksheet.batch_update(cells_to_update)

            st.cache_data.clear()
            return True

        except Exception as e:
            st.error(f"Failed to update Database: {e}")
            return False

        
# ── Helper: Filter Data by User Location ─────────────────
def filter_by_user_location(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    user_role = str(st.session_state.get("user_role", "")).strip().lower()
    if user_role in ("admin", "super admin", "superadmin"):
        return df

    raw_locations = st.session_state.get("user_regions") or st.session_state.get("user_locations") or st.session_state.get("location", "")
    if not raw_locations:
        st.warning("⚠️ No assigned location found in session state.")
        return pd.DataFrame(columns=df.columns)

    allowed_locations = set()
    if isinstance(raw_locations, str):
        allowed_locations = {loc.strip().lower() for loc in raw_locations.split(",") if loc.strip()}
    elif isinstance(raw_locations, (list, tuple, set)):
        allowed_locations = {str(loc).strip().lower() for loc in raw_locations if str(loc).strip()}

    loc_col = next((c for c in df.columns if c.strip().lower() == "location_name"), None)
    if loc_col and allowed_locations:
        clean_col = df[loc_col].astype(str).str.strip().str.lower()
        return df[clean_col.isin(allowed_locations)]

    st.error("⚠️ Column 'location_name' was not found in dataset.")
    return pd.DataFrame(columns=df.columns)


# ── Helper: Calculate Freight Percentages & Totals ───────────────
def calculate_freight_percentages(df: pd.DataFrame) -> pd.DataFrame:
    inv_val = pd.to_numeric(df.get("invoiced_value_fc"), errors="coerce").fillna(0)
    prov_amt = pd.to_numeric(df.get("provisional_freight_amount"), errors="coerce").fillna(0)
    act_amt = pd.to_numeric(df.get("actual_freight_amount"), errors="coerce").fillna(0)

    lr = pd.to_numeric(df.get("lr_charges"), errors="coerce").fillna(0)
    loading = pd.to_numeric(df.get("loading_charges"), errors="coerce").fillna(0)
    unloading = pd.to_numeric(df.get("unloading_charges"), errors="coerce").fillna(0)
    detention = pd.to_numeric(df.get("detension_charges"), errors="coerce").fillna(0)
    point = pd.to_numeric(df.get("point_charges"), errors="coerce").fillna(0)
    approx_dist = pd.to_numeric(df.get("approx_distance"), errors="coerce").fillna(0)

    df["provisional_perc"] = np.where(inv_val > 0, np.round((prov_amt / inv_val) * 100, 2), 0.0)
    df["actual_perc"] = np.where(inv_val > 0, np.round((act_amt / inv_val) * 100, 2), 0.0)

    df["total_freight_cost"] = np.round(act_amt + lr + loading + unloading + detention + point, 2)
    df["cost_per_km"] = np.where(
        approx_dist > 0,
        np.round(df["total_freight_cost"] / approx_dist, 2),
        0.0
    )

    return df


# ── Main Dashboard Renderer ──────────────────────────────
def render_dashboard_page(sales_db: str):
    st.title("📌 Logistics Dashboard")

    PAGE_SIZE = 10

    if "editor_key_version" not in st.session_state:
        st.session_state.editor_key_version = 0

    # Auto-Refresh Logic (300 seconds)
    if "last_auto_refresh" not in st.session_state:
        st.session_state.last_auto_refresh = time.time()

    if time.time() - st.session_state.last_auto_refresh > 300:
        if not st.session_state.get("pending_edits"):
            st.session_state.last_auto_refresh = time.time()
            st.session_state.pop("working_df", None)
            st.cache_data.clear()
            st.rerun()

    # Data Initialization
    if "working_df" not in st.session_state:
        try:
            raw_df = load_worksheet_data(sales_db)
        except Exception as e:
            st.error(f"Error loading worksheet '{sales_db}': {e}")
            show_popup(f"Error loading worksheet '{sales_db}': {e}", type="error")
            raw_df = pd.DataFrame()

        if raw_df.empty:
            st.warning(f"No records found in worksheet: **{sales_db}**")
            return

        filtered = filter_by_user_location(raw_df)
        if filtered.empty:
            st.info("No records available for your assigned location(s).")
            return

        for col in DEFAULT_COLUMNS:
            if col not in filtered.columns:
                filtered[col] = ""

        if "bill_date" in filtered.columns:
            filtered["bill_date"] = pd.to_datetime(filtered["bill_date"], errors="coerce").dt.date

        numeric_cols = [
            "approx_distance", "provisional_freight_amount", "actual_freight_amount", "lr_charges",
            "loading_charges", "unloading_charges", "detension_charges", "point_charges", "invoiced_value_fc"
        ]
        
        for n_col in numeric_cols:
            if n_col in filtered.columns:
                filtered[n_col] = pd.to_numeric(filtered[n_col], errors="coerce").fillna(0.0)

        filtered = calculate_freight_percentages(filtered)
        st.session_state.working_df = filtered.copy()

    if "pending_edits" not in st.session_state:
        st.session_state.pending_edits = {}

    df_work = st.session_state.working_df
    col_id = next((c for c in df_work.columns if c.strip().lower() == "id"), "id")

    # Filters Section
    st.markdown("### 🔍 Filter Records")
    col_loc = next((c for c in df_work.columns if c.strip().lower() == "location_name"), None)
    col_pos = next((c for c in df_work.columns if c.strip().lower() == "place_of_supply"), None)
    col_trans = next((c for c in df_work.columns if c.strip().lower() == "transporter_name"), None)
    col_date = next((c for c in df_work.columns if c.strip().lower() == "invoice_doc_date"), None)

    temp_df = df_work.copy()
    if col_date:
        temp_df["parsed_invoice_date"] = pd.to_datetime(temp_df[col_date], format="%d/%m/%Y", errors="coerce")
        if temp_df["parsed_invoice_date"].isna().all():
            temp_df["parsed_invoice_date"] = pd.to_datetime(temp_df[col_date], errors="coerce")

    f1, f2, f3 = st.columns(3)
    with f1:
        loc_opts = sorted(temp_df[col_loc].dropna().astype(str).unique()) if col_loc else []
        selected_locations = st.multiselect("Location Name", options=loc_opts)
    with f2:
        pos_opts = sorted(temp_df[col_pos].dropna().astype(str).unique()) if col_pos else []
        selected_pos = st.multiselect("Place of Supply", options=pos_opts)
    with f3:
        trans_opts = sorted(temp_df[col_trans].dropna().astype(str).unique()) if col_trans else []
        selected_transporters = st.multiselect("Transporter Name", options=trans_opts)

    d1, d2, d3 = st.columns([2, 2, 1])
    with d1:
        from_date = st.date_input("From Date", value=None)
    with d2:
        to_date = st.date_input("To Date", value=None)
    with d3:
        st.write("")
        st.write("")
        if st.button("🔄 Refresh Data", width="stretch", type="secondary"):
            st.session_state.pop("working_df", None)
            st.session_state.pop("pending_edits", None)
            st.cache_data.clear()
            st.session_state.last_auto_refresh = time.time()
            st.rerun()

    filtered_df = temp_df.copy()
    if col_loc and selected_locations:
        filtered_df = filtered_df[filtered_df[col_loc].astype(str).isin(selected_locations)]
    if col_pos and selected_pos:
        filtered_df = filtered_df[filtered_df[col_pos].astype(str).isin(selected_pos)]
    if col_trans and selected_transporters:
        filtered_df = filtered_df[filtered_df[col_trans].astype(str).isin(selected_transporters)]
    if col_date and "parsed_invoice_date" in filtered_df:
        if from_date:
            filtered_df = filtered_df[filtered_df["parsed_invoice_date"].dt.date >= from_date]
        if to_date:
            filtered_df = filtered_df[filtered_df["parsed_invoice_date"].dt.date <= to_date]

    st.markdown("---")

    # Pagination Logic
    total_rows = len(filtered_df)
    total_pages = max(1, int(np.ceil(total_rows / PAGE_SIZE)))

    if "current_page" not in st.session_state:
        st.session_state.current_page = 1

    if st.session_state.current_page > total_pages:
        st.session_state.current_page = total_pages

    start_idx = (st.session_state.current_page - 1) * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, total_rows)

    # ── Process Data Edits BEFORE UI Data Preparation ──────────────
    editor_key = f"data_editor_grid_{st.session_state.editor_key_version}"
    editor_state = st.session_state.get(editor_key, {})
    grid_edited_rows = editor_state.get("edited_rows", {})

    if grid_edited_rows:
        page_slice = filtered_df[DEFAULT_COLUMNS].iloc[start_idx:end_idx]
        illegal_edit_intercepted = False

        for row_idx_str, changes in grid_edited_rows.items():
            row_idx = int(row_idx_str)
            if row_idx < len(page_slice):
                current_row = page_slice.iloc[row_idx]
                record_id = str(current_row[col_id]).strip()

                # Determine the exact current bill_no across pending edits, working_df, or fallback row
                working_match = st.session_state.working_df[
                    st.session_state.working_df[col_id].astype(str).str.strip() == record_id
                ]
                working_bill = working_match["bill_no"].values[0] if not working_match.empty else current_row.get("bill_no", "")
                
                pending_record = st.session_state.pending_edits.get(record_id, {})
                existing_bill = pending_record.get("bill_no", working_bill)
                new_bill = changes.get("bill_no", existing_bill)

                has_bill_no = (
                    bool(str(new_bill).strip()) 
                    and str(new_bill).strip().lower() not in ("nan", "none", "<na>")
                )

                valid_changes = {}
                blocked_cols = []

                if "bill_no" in changes:
                    valid_changes["bill_no"] = changes["bill_no"]

                for col_k, val_v in changes.items():
                    if col_k == "bill_no":
                        continue
                    # Check if user attempts to edit a restricted column without a Bill No
                    if col_k in CONDITIONAL_COLS and not has_bill_no and float(val_v or 0.0) != 0.0:
                        blocked_cols.append(col_k.replace("_", " ").title())
                        valid_changes[col_k] = 0.0
                        illegal_edit_intercepted = True
                    else:
                        valid_changes[col_k] = val_v

                if blocked_cols:
                    st.toast(
                        f"⚠️ **ID #{record_id}**: Enter **Bill No** first before adding charges: {', '.join(blocked_cols)}.", 
                        icon="🚫"
                    )

                if valid_changes:
                    if record_id not in st.session_state.pending_edits:
                        st.session_state.pending_edits[record_id] = {}

                    st.session_state.pending_edits[record_id].update(valid_changes)

                    # Update working dataframe locally
                    main_mask = (
                        st.session_state.working_df[col_id]
                        .astype(str)
                        .str.strip()
                        == record_id
                    )
                    for k, v in valid_changes.items():
                        st.session_state.working_df.loc[main_mask, k] = v

                    # Recalculate percentages & totals
                    st.session_state.working_df = calculate_freight_percentages(
                        st.session_state.working_df
                    )

                    # Update calculated columns into pending edits
                    updated_row = st.session_state.working_df[main_mask].iloc[0]
                    for calc_col in ["provisional_perc", "actual_perc", "total_freight_cost", "cost_per_km"]:
                        if calc_col in updated_row:
                            st.session_state.pending_edits[record_id][calc_col] = updated_row[calc_col]

        # Sync working DF back into local filter subset
        filtered_df.update(st.session_state.working_df)

        # If illegal edits occurred, bump editor version key to force UI state reset
        if illegal_edit_intercepted:
            st.session_state.editor_key_version += 1
            st.rerun()

    # Top Controls Rendering
    top_col1, top_col2, top_col3 = st.columns([1, 2, 1])
    with top_col1:
        selected_top_page = st.number_input(
            "Go to Page",
            min_value=1,
            max_value=total_pages,
            value=st.session_state.current_page,
            step=1,
            key="top_page_input"
        )
        if selected_top_page != st.session_state.current_page:
            st.session_state.current_page = selected_top_page
            st.rerun()

    with top_col2:
        st.markdown(
            f"<div style='text-align: center; margin-top: 28px; font-weight: 500; color: #555;'>"
            f"Page <b>{st.session_state.current_page}</b> of <b>{total_pages}</b> &nbsp;|&nbsp; "
            f"Showing rows <b>{start_idx + 1 if total_rows > 0 else 0}-{end_idx}</b> of <b>{total_rows}</b>"
            f"</div>",
            unsafe_allow_html=True
        )

    with top_col3:
        st.markdown("<br>", unsafe_allow_html=True)
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            filtered_df.to_excel(writer, index=False, sheet_name="Logistics Data")

        st.download_button(
            label="📥 Download Filtered Data (Excel)",
            data=excel_buffer.getvalue(),
            file_name="logistics_filtered_data.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="secondary"
        )

    # ── Display Table Setup ─────────────────────────────────────
    display_df = filtered_df[DEFAULT_COLUMNS].copy()
    
    if "challan_no" in display_df.columns:
        display_df["challan_no"] = display_df["challan_no"].fillna("").astype(str).replace(["nan", "None", "<NA>"], "")

    if "bill_no" in display_df.columns:
        display_df["bill_no"] = display_df["bill_no"].fillna("").astype(str)

    page_data = display_df.iloc[start_idx:end_idx].copy()

    column_configuration = {
        col: st.column_config.Column(
            label=col.replace("_", " ").title(), disabled=True
        )
        for col in display_df.columns
    }

    column_configuration.update({
        col_id: st.column_config.Column("ID", disabled=True),
        "provisional_perc": st.column_config.NumberColumn(
            "Provisional %", disabled=True, format="%.2f%%"
        ),
        "actual_perc": st.column_config.NumberColumn(
            "Actual %", disabled=True, format="%.2f%%"
        ),
        "total_freight_cost": st.column_config.NumberColumn(
            "Total Freight Cost", disabled=True, format="%.2f"
        ),
        "cost_per_km": st.column_config.NumberColumn(
            "Cost Per KM", disabled=True, format="%.2f"
        ),
        "challan_no": st.column_config.TextColumn(
            "Challan No",
            disabled=False,
            help="Enter alphanumeric Challan Number",
            default="",
        ),
        "vehicle_type": st.column_config.SelectboxColumn(
            "Vehicle Type",
            options=[
                "By Hand",
                "Courier",
                "Part Load",
                "Tata Ace / Pickup 9 ft",
                "14 feet",
                "17 feet",
                "19/20 feet",
                "22/24 feet",
                "32 SXL 7 ton",
                "32 SXL 9 ton",
            ],
            required=False,
            disabled=False,
        ),
        "approx_distance": st.column_config.NumberColumn(
            "Approx Distance", format="%d", disabled=False
        ),
        "provisional_freight_amount": st.column_config.NumberColumn(
            "Provisional Freight Amt", format="%.2f", disabled=False
        ),
        "actual_freight_amount": st.column_config.NumberColumn(
            "Actual Freight Amt",
            format="%.2f",
            disabled=False,
            help="Requires Bill No first before editing non-zero amounts",
        ),
        "unloading_charges": st.column_config.NumberColumn(
            "Unloading Charges",
            format="%.2f",
            disabled=False,
            help="Requires Bill No first before editing non-zero amounts",
        ),
        "detension_charges": st.column_config.NumberColumn(
            "Detention Charges",
            format="%.2f",
            disabled=False,
            help="Requires Bill No first before editing non-zero amounts",
        ),
        "point_charges": st.column_config.NumberColumn(
            "Point Charges",
            format="%.2f",
            disabled=False,
            help="Requires Bill No first before editing non-zero amounts",
        ),
        "lr_charges": st.column_config.NumberColumn(
            "LR Charges", format="%.2f", disabled=False
        ),
        "loading_charges": st.column_config.NumberColumn(
            "Loading Charges", format="%.2f", disabled=False
        ),
        "po_no": st.column_config.TextColumn(
            "PO No", disabled=False, default=""
        ),
        "bill_no": st.column_config.TextColumn(
            "Bill No", disabled=False, default=""
        ),
        "bill_date": st.column_config.DateColumn(
            "Bill Date", format="YYYY-MM-DD", disabled=False
        ),
        "bill_receiving_status": st.column_config.SelectboxColumn(
            "Bill Receiving Status",
            options=["Received", "Pending", "Rejected"],
            required=False,
            disabled=False,
        ),
        "remark": st.column_config.TextColumn("Remark", disabled=False),
    })

    st.markdown("##### ✏️ Double click any cell below to edit value:")

    # Render table with dynamic key versioning to allow instant resets
    st.data_editor(
        page_data,
        column_config=column_configuration,
        width="stretch",
        hide_index=True,
        key=f"data_editor_grid_{st.session_state.editor_key_version}"
    )

    # Database Save Operations
    if st.session_state.pending_edits:
        st.info(f"📝 You have unsaved changes for **{len(st.session_state.pending_edits)}** record(s).")
        if st.button("💾 Save Changes to Database", type="primary"):
            success = update_gsheet_atomic(
                sheet_name=sales_db,
                id_col=col_id,
                pending_edits=st.session_state.pending_edits,
                required_columns=DEFAULT_COLUMNS
            )
            if success:
                st.success("✅ Changes saved to Database successfully!")
                st.session_state.pending_edits = {}
                st.session_state.pop("working_df", None)
                time.sleep(1)
                st.rerun()



# import io
# import time
# import threading
# import numpy as np
# import pandas as pd
# import streamlit as st
# from main import connect_gsheet, show_popup

# # Global thread lock for synchronized GSheet updates across concurrent sessions
# DB_LOCK = threading.Lock()


# # ── Helper: Fetch Worksheet Data ─────────────────────────
# # Removed st UI elements from inside cache function to fix CacheReplayClosureError
# @st.cache_data(ttl=30)
# def load_worksheet_data(sheet_name: str) -> pd.DataFrame:
#     spreadsheet = connect_gsheet()
#     worksheet = spreadsheet.worksheet(sheet_name)
#     data = worksheet.get_all_records()
#     return pd.DataFrame(data)


# def gspread_cell_format(row: int, col: int) -> str:
#     col_str = ""
#     while col > 0:
#         col, remainder = divmod(col - 1, 26)
#         col_str = chr(65 + remainder) + col_str
#     return f"{col_str}{row}"


# # ── Helper: Atomic & Concurrent-Safe Google Sheet Updates ─────
# def update_gsheet_atomic(sheet_name: str, id_col: str, pending_edits: dict) -> bool:
#     """
#     Safely updates Google Sheets in a multi-user environment.
#     Only writes changed fields recorded in memory (pending_edits) inside a thread lock.
#     """
#     if not pending_edits:
#         return True

#     with DB_LOCK:
#         try:
#             spreadsheet = connect_gsheet()
#             worksheet = spreadsheet.worksheet(sheet_name)

#             # Fetch current headers live inside the lock
#             raw_headers = list(worksheet.row_values(1))
#             headers_lookup = [str(h).strip().lower() for h in raw_headers]
#             id_col_clean = id_col.strip().lower()

#             if id_col_clean not in headers_lookup:
#                 st.error(f"Column '{id_col}' not found in Database headers.")
#                 return False

#             id_col_idx = headers_lookup.index(id_col_clean) + 1

#             # Check if any new keys in pending_edits need new header columns
#             new_cols_added = False
#             for record_id, changes in pending_edits.items():
#                 for col_name in changes.keys():
#                     if col_name == "parsed_invoice_date":
#                         continue
#                     clean_name = str(col_name).strip().lower()
#                     if clean_name not in headers_lookup:
#                         raw_headers.append(str(col_name))
#                         headers_lookup.append(clean_name)
#                         new_cols_added = True

#             if new_cols_added:
#                 worksheet.update("1:1", [raw_headers])

#             # Find cell rows dynamically for requested record IDs
#             cells_to_update = []

#             for record_id, changes in pending_edits.items():
#                 matching_cell = worksheet.find(str(record_id), in_column=id_col_idx)
#                 if not matching_cell:
#                     st.warning(f"⚠️ Row for ID #{record_id} not found in Google Sheet.")
#                     continue

#                 target_row = matching_cell.row

#                 for col_name, new_val in changes.items():
#                     if col_name == "parsed_invoice_date":
#                         continue

#                     col_name_clean = str(col_name).strip().lower()
#                     if col_name_clean in headers_lookup:
#                         col_idx = headers_lookup.index(col_name_clean) + 1

#                         if pd.isna(new_val) or new_val is None:
#                             val_str = ""
#                         elif isinstance(new_val, (pd.Timestamp, pd.DatetimeIndex)):
#                             val_str = new_val.strftime("%Y-%m-%d")
#                         else:
#                             val_str = str(new_val)

#                         cells_to_update.append({
#                             "range": gspread_cell_format(target_row, col_idx),
#                             "values": [[val_str]]
#                         })

#             if cells_to_update:
#                 worksheet.batch_update(cells_to_update)

#             st.cache_data.clear()
#             return True

#         except Exception as e:
#             st.error(f"Failed to update Database: {e}")
#             return False


# # ── Helper: Filter Data by User Location ─────────────────
# def filter_by_user_location(df: pd.DataFrame) -> pd.DataFrame:
#     if df.empty:
#         return df

#     user_role = str(st.session_state.get("user_role", "")).strip().lower()
#     if user_role in ("admin", "super admin", "superadmin"):
#         return df

#     raw_locations = st.session_state.get("user_regions") or st.session_state.get("user_locations") or st.session_state.get("location", "")
#     if not raw_locations:
#         st.warning("⚠️ No assigned location found in session state.")
#         return pd.DataFrame(columns=df.columns)

#     allowed_locations = set()
#     if isinstance(raw_locations, str):
#         allowed_locations = {loc.strip().lower() for loc in raw_locations.split(",") if loc.strip()}
#     elif isinstance(raw_locations, (list, tuple, set)):
#         allowed_locations = {str(loc).strip().lower() for loc in raw_locations if str(loc).strip()}

#     loc_col = next((c for c in df.columns if c.strip().lower() == "location_name"), None)
#     if loc_col and allowed_locations:
#         clean_col = df[loc_col].astype(str).str.strip().str.lower()
#         return df[clean_col.isin(allowed_locations)]

#     st.error("⚠️ Column 'location_name' was not found in dataset.")
#     return pd.DataFrame(columns=df.columns)


# # ── Helper: Calculate Freight Percentages ────────────────
# def calculate_freight_percentages(df: pd.DataFrame) -> pd.DataFrame:
#     inv_val = pd.to_numeric(df.get("invoiced_value_fc"), errors="coerce").fillna(0)
#     prov_amt = pd.to_numeric(df.get("provisional_freight_amount"), errors="coerce").fillna(0)
#     act_amt = pd.to_numeric(df.get("actual_freight_amount"), errors="coerce").fillna(0)

#     df["provisional_perc"] = np.where(inv_val > 0, np.round((prov_amt / inv_val) * 100, 2), 0.0)
#     df["actual_perc"] = np.where(inv_val > 0, np.round((act_amt / inv_val) * 100, 2), 0.0)
#     return df


# # ── Main Dashboard Renderer ──────────────────────────────
# def render_dashboard_page(sales_db: str):
#     st.title("📌 Logistics Dashboard")

#     PAGE_SIZE = 10

#     DEFAULT_COLUMNS = [
#         "id", "location_name", "invoice_type_desc", "invoice_doc_date", "tax_invoice_no",
#         "customer_name", "place_of_supply", "fg_qty", "selling_fc_value", "igst",
#         "cgst", "sgst", "invoiced_value_fc", "challan_no", "vehicle_no", "eway_bill_no",
#         "vehicle_type", "approx_distance", "provisional_freight_amount",
#         "provisional_perc", "actual_freight_amount", "actual_perc", "loading_charges",
#         "unloading_charges", "detension_charges", "po_no", "bill_no", "bill_date",
#         "bill_receiving_status"
#     ]

#     # Auto-Refresh Logic (Set to 300s to avoid dropping working edits prematurely)
#     if "last_auto_refresh" not in st.session_state:
#         st.session_state.last_auto_refresh = time.time()

#     if time.time() - st.session_state.last_auto_refresh > 300:
#         if not st.session_state.get("pending_edits"):
#             st.session_state.last_auto_refresh = time.time()
#             st.session_state.pop("working_df", None)
#             st.cache_data.clear()
#             st.rerun()

#     # Data Initialization
#     if "working_df" not in st.session_state:
#         try:
#             raw_df = load_worksheet_data(sales_db)
#         except Exception as e:
#             st.error(f"Error loading worksheet '{sales_db}': {e}")
#             show_popup(f"Error loading worksheet '{sales_db}': {e}", type="error")
#             raw_df = pd.DataFrame()

#         if raw_df.empty:
#             st.warning(f"No records found in worksheet: **{sales_db}**")
#             return

#         filtered = filter_by_user_location(raw_df)
#         if filtered.empty:
#             st.info("No records available for your assigned location(s).")
#             return

#         for col in DEFAULT_COLUMNS:
#             if col not in filtered.columns:
#                 filtered[col] = ""

#         if "bill_date" in filtered.columns:
#             filtered["bill_date"] = pd.to_datetime(filtered["bill_date"], errors="coerce").dt.date

#         numeric_cols = [
#             "approx_distance", "provisional_freight_amount", "actual_freight_amount",
#             "loading_charges", "unloading_charges", "detension_charges", "invoiced_value_fc"
#         ]
#         for n_col in numeric_cols:
#             if n_col in filtered.columns:
#                 filtered[n_col] = pd.to_numeric(filtered[n_col], errors="coerce").fillna(0.0)

#         filtered = calculate_freight_percentages(filtered)
#         st.session_state.working_df = filtered.copy()

#     if "pending_edits" not in st.session_state:
#         st.session_state.pending_edits = {}

#     df_work = st.session_state.working_df
#     col_id = next((c for c in df_work.columns if c.strip().lower() == "id"), "id")

#     # Filters
#     st.markdown("### 🔍 Filter Records")
#     col_loc = next((c for c in df_work.columns if c.strip().lower() == "location_name"), None)
#     col_cust = next((c for c in df_work.columns if c.strip().lower() == "customer_name"), None)
#     col_pos = next((c for c in df_work.columns if c.strip().lower() == "place_of_supply"), None)
#     col_trans = next((c for c in df_work.columns if c.strip().lower() == "transporter_name"), None)
#     col_date = next((c for c in df_work.columns if c.strip().lower() == "invoice_doc_date"), None)

#     temp_df = df_work.copy()
#     if col_date:
#         temp_df["parsed_invoice_date"] = pd.to_datetime(temp_df[col_date], format="%d/%m/%Y", errors="coerce")
#         if temp_df["parsed_invoice_date"].isna().all():
#             temp_df["parsed_invoice_date"] = pd.to_datetime(temp_df[col_date], errors="coerce")

#     f1, f2, f3, f4 = st.columns(4)
#     with f1:
#         loc_opts = sorted(temp_df[col_loc].dropna().astype(str).unique()) if col_loc else []
#         selected_locations = st.multiselect("Location Name", options=loc_opts)
#     with f2:
#         cust_opts = sorted(temp_df[col_cust].dropna().astype(str).unique()) if col_cust else []
#         selected_customers = st.multiselect("Customer Name", options=cust_opts)
#     with f3:
#         pos_opts = sorted(temp_df[col_pos].dropna().astype(str).unique()) if col_pos else []
#         selected_pos = st.multiselect("Place of Supply", options=pos_opts)
#     with f4:
#         trans_opts = sorted(temp_df[col_trans].dropna().astype(str).unique()) if col_trans else []
#         selected_transporters = st.multiselect("Transporter Name", options=trans_opts)

#     d1, d2, d3 = st.columns([2, 2, 1])
#     with d1:
#         from_date = st.date_input("From Date", value=None)
#     with d2:
#         to_date = st.date_input("To Date", value=None)
#     with d3:
#         st.write("")
#         st.write("")
#         if st.button("🔄 Refresh Data", width="stretch", type="secondary"):
#             st.session_state.pop("working_df", None)
#             st.session_state.pop("pending_edits", None)
#             st.cache_data.clear()
#             st.session_state.last_auto_refresh = time.time()
#             st.rerun()

#     filtered_df = temp_df.copy()
#     if col_loc and selected_locations:
#         filtered_df = filtered_df[filtered_df[col_loc].astype(str).isin(selected_locations)]
#     if col_cust and selected_customers:
#         filtered_df = filtered_df[filtered_df[col_cust].astype(str).isin(selected_customers)]
#     if col_pos and selected_pos:
#         filtered_df = filtered_df[filtered_df[col_pos].astype(str).isin(selected_pos)]
#     if col_trans and selected_transporters:
#         filtered_df = filtered_df[filtered_df[col_trans].astype(str).isin(selected_transporters)]
#     if col_date and "parsed_invoice_date" in filtered_df:
#         if from_date:
#             filtered_df = filtered_df[filtered_df["parsed_invoice_date"].dt.date >= from_date]
#         if to_date:
#             filtered_df = filtered_df[filtered_df["parsed_invoice_date"].dt.date <= to_date]

#     st.markdown("---")

#     # Pagination
#     total_rows = len(filtered_df)
#     total_pages = max(1, int(np.ceil(total_rows / PAGE_SIZE)))

#     if "current_page" not in st.session_state:
#         st.session_state.current_page = 1

#     if st.session_state.current_page > total_pages:
#         st.session_state.current_page = total_pages

#     top_col1, top_col2, top_col3 = st.columns([1, 2, 1])
#     with top_col1:
#         selected_top_page = st.number_input(
#             "Go to Page",
#             min_value=1,
#             max_value=total_pages,
#             value=st.session_state.current_page,
#             step=1,
#             key="top_page_input"
#         )
#         if selected_top_page != st.session_state.current_page:
#             st.session_state.current_page = selected_top_page
#             st.rerun()

#     with top_col2:
#         start_idx = (st.session_state.current_page - 1) * PAGE_SIZE
#         end_idx = min(start_idx + PAGE_SIZE, total_rows)
#         st.markdown(
#             f"<div style='text-align: center; margin-top: 28px; font-weight: 500; color: #555;'>"
#             f"Page <b>{st.session_state.current_page}</b> of <b>{total_pages}</b> &nbsp;|&nbsp; "
#             f"Showing rows <b>{start_idx + 1 if total_rows > 0 else 0}-{end_idx}</b> of <b>{total_rows}</b>"
#             f"</div>",
#             unsafe_allow_html=True
#         )

#     with top_col3:
#         st.markdown("<br>", unsafe_allow_html=True)
#         export_df = filtered_df
#         excel_buffer = io.BytesIO()
#         with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
#             export_df.to_excel(writer, index=False, sheet_name="Logistics Data")

#         st.download_button(
#             label="📥 Download Filtered Data (Excel)",
#             data=excel_buffer.getvalue(),
#             file_name="logistics_filtered_data.xlsx",
#             mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
#             type="secondary"
#         )

#     # 1. Capture edits from previous event before slicing display data
#     editor_state = st.session_state.get("data_editor_grid", {})
#     grid_edited_rows = editor_state.get("edited_rows", {})

#     # Apply edits directly to memory (`working_df` & `pending_edits`)
#     if grid_edited_rows:
#         temp_page_slice = filtered_df[DEFAULT_COLUMNS].iloc[start_idx:end_idx]
#         for row_idx_str, changes in grid_edited_rows.items():
#             row_idx = int(row_idx_str)
#             if row_idx < len(temp_page_slice):
#                 record_id = str(temp_page_slice.iloc[row_idx][col_id]).strip()
#                 if record_id not in st.session_state.pending_edits:
#                     st.session_state.pending_edits[record_id] = {}
#                 st.session_state.pending_edits[record_id].update(changes)

#                 main_mask = st.session_state.working_df[col_id].astype(str).str.strip() == record_id
#                 for k, v in changes.items():
#                     st.session_state.working_df.loc[main_mask, k] = v

#         st.session_state.working_df = calculate_freight_percentages(st.session_state.working_df)
#         filtered_df.update(st.session_state.working_df)

#     # 2. Slice Page Data AFTER calculations update
#     display_df = filtered_df[DEFAULT_COLUMNS]
#     page_data = display_df.iloc[start_idx:end_idx].copy()

#     column_configuration = {
#         col: st.column_config.Column(
#             label=col.replace("_", " ").title(),
#             disabled=True
#         )
#         for col in display_df.columns
#     }

#     column_configuration.update({
#         col_id: st.column_config.Column("ID", disabled=True),
#         "provisional_perc": st.column_config.NumberColumn("Provisional %", disabled=True, format="%.2f%%"),
#         "actual_perc": st.column_config.NumberColumn("Actual %", disabled=True, format="%.2f%%"),
#         "vehicle_type": st.column_config.SelectboxColumn(
#             "Vehicle Type",
#             options=["By Hand", "Courier", "Part Load", "Tata Ace / Pickup 9 ft", "14 feet", "17 feet", "19/20 feet", "22/24 feet", "32 SXL 7 ton", "32 SXL 9 ton"],
#             required=False,
#             disabled=False
#         ),
#         "approx_distance": st.column_config.NumberColumn("Approx Distance", format="%d", disabled=False),
#         "provisional_freight_amount": st.column_config.NumberColumn("Provisional Freight Amt", format="%.2f", disabled=False),
#         "actual_freight_amount": st.column_config.NumberColumn("Actual Freight Amt", format="%.2f", disabled=False),
#         "loading_charges": st.column_config.NumberColumn("Loading Charges", format="%.2f", disabled=False),
#         "unloading_charges": st.column_config.NumberColumn("Unloading Charges", format="%.2f", disabled=False),
#         "detension_charges": st.column_config.NumberColumn("Detention Charges", format="%.2f", disabled=False),
#         "po_no": st.column_config.TextColumn("PO No", disabled=False),
#         "bill_no": st.column_config.TextColumn("Bill No", disabled=False),
#         "bill_date": st.column_config.DateColumn("Bill Date", format="YYYY-MM-DD", disabled=False),
#         "bill_receiving_status": st.column_config.SelectboxColumn(
#             "Bill Receiving Status",
#             options=["Received", "Pending", "Rejected"],
#             required=False,
#             disabled=False
#         )
#     })

#     st.markdown("##### ✏️ Double click any cell below to edit value:")

#     edited_df = st.data_editor(
#         page_data,
#         column_config=column_configuration,
#         width="stretch",
#         hide_index=True,
#         key="data_editor_grid"
#     )

#     # Persistent Save Button (Thread-Safe Operation)
#     if st.session_state.pending_edits:
#         st.info(f"📝 You have unsaved changes for **{len(st.session_state.pending_edits)}** record(s).")
#         if st.button("💾 Save Changes to Database", type="primary"):
#             success = update_gsheet_atomic(
#                 sheet_name=sales_db,
#                 id_col=col_id,
#                 pending_edits=st.session_state.pending_edits
#             )
#             if success:
#                 st.success("✅ Changes saved to Database successfully!")
#                 st.session_state.pending_edits = {}
#                 st.session_state.pop("working_df", None)
#                 time.sleep(1)
#                 st.rerun()
