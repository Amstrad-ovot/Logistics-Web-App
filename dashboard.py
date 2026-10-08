import re
import io
import time
import threading
import numpy as np
import pandas as pd
import streamlit as st
from main import connect_gsheet, gsheet_call, show_popup

# Global thread lock for synchronized GSheet updates across concurrent sessions
DB_LOCK = threading.Lock()

# ─────────────────────────────────────────────────────────────────────────────
# 1. Dashboard Configurations
# ─────────────────────────────────────────────────────────────────────────────
DASHBOARD_CONFIGS = {
    "purchase": {
        "title": "Purchase Logistics Dashboard",
        "default_columns": [
            "id", "location_name", "item_category", "grn_document_date", "grn_document_no", "purchaseorsrt",
            "supplier_name", "grn_qty", "basic", "cgst_input_9%", "igst_input_18%", "sgst_input_9%",
            "total", "vehicle_no", "challan_no", "transporter_name", "vehicle_type", "approx_distance",
            "provisional_freight_amount", "provisional_perc", "actual_freight_amount", "actual_perc",
            "lr_charges", "loading_charges", "unloading_charges", "detension_charges", "point_charges",
            "total_freight_cost", "cost_per_km", "po_no", "bill_no", "bill_date", "bill_receiving_status", "remark"
        ],
        "conditional_cols": {
            "actual_freight_amount",
            "unloading_charges",
            "detension_charges",
            "point_charges",
        },
        "doc_date_col": "grn_document_date",
        "doc_no_col": "grn_document_no",
        "group_col": "group_display_name",
        "cat_col": "item_category",
        "export_filename": "purchase_logistics_data.xlsx"
    },
    "sales": {
        "title": "Sales Logistics Dashboard",
        "default_columns": [
            "id", "location_name", "invoice_type_desc", "invoice_doc_date", "tax_invoice_no",
                "customer_name", "cust_city_name", "place_of_supply", "fg_qty", "selling_fc_value", "igst",
                "cgst", "sgst", "invoiced_value_fc", "vehicle_no", "eway_bill_no", "transporter_name", "challan_no",
                "vehicle_type", "approx_distance", "provisional_freight_amount",
                "provisional_perc", "actual_freight_amount", "actual_perc", "lr_charges", "loading_charges",
                "unloading_charges", "detension_charges", "point_charges", "total_freight_cost", 
                "cost_per_km", "po_no", "bill_no", "bill_date", "bill_receiving_status", "remark"
        ],
        "conditional_cols": {
            "actual_freight_amount",
            "unloading_charges",
            "detension_charges",
            "point_charges",
        },
        "doc_date_col": "invoice_doc_date",
        "doc_no_col": "tax_invoice_no",
        "group_col": "place_of_supply",
        "cat_col": "cust_city_name",
        "export_filename": "sales_logistics_data.xlsx"
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. Data Helpers & Cache Mechanics
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def load_worksheet_data(sheet_name: str) -> pd.DataFrame:
    spreadsheet = connect_gsheet()
    worksheet = gsheet_call(lambda: spreadsheet.worksheet(sheet_name))
    data = gsheet_call(lambda: worksheet.get_all_records())
    return pd.DataFrame(data)


def gspread_cell_format(row: int, col: int) -> str:
    col_str = ""
    while col > 0:
        col, remainder = divmod(col - 1, 26)
        col_str = chr(65 + remainder) + col_str
    return f"{col_str}{row}"


def update_gsheet_atomic(sheet_name: str, id_col: str, pending_edits: dict, required_columns: list = None) -> bool:
    if not pending_edits and not required_columns:
        return True

    with DB_LOCK:
        try:
            spreadsheet = connect_gsheet()
            worksheet = gsheet_call(lambda: spreadsheet.worksheet(sheet_name))

            raw_headers = list(gsheet_call(lambda: worksheet.row_values(1)))
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
            column_ids = gsheet_call(lambda: worksheet.col_values(id_col_idx))
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
                gsheet_call(lambda: worksheet.batch_update(cells_to_update))

            st.cache_data.clear()
            return True

        except Exception as e:
            st.error(f"Failed to update Database: {e}")
            return False


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
        escaped_locations = [re.escape(loc) for loc in allowed_locations]
        pattern = "|".join(escaped_locations)
        mask = df[loc_col].astype(str).str.contains(pattern, case=False, na=False)
        return df[mask]

    st.error("⚠️ Column 'location_name' was not found in dataset.")
    return pd.DataFrame(columns=df.columns)


def calculate_freight_percentages(df: pd.DataFrame) -> pd.DataFrame:
    # Handle Sales ("invoiced_value_fc") vs Purchase/Inward ("total") column names
    if "invoiced_value_fc" in df.columns:
        inv_col = "invoiced_value_fc"
    elif "total" in df.columns:
        inv_col = "total"
    inv_val = pd.to_numeric(df.get(inv_col), errors="coerce").fillna(0)
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
        0.0)

    return df


def calculate_freight_row(df: pd.DataFrame, row_idx):
    """Calculate derived freight fields for one edited row only."""
    def num(col):
        if col not in df.columns:
            return 0.0
        value = pd.to_numeric(df.at[row_idx, col], errors="coerce")
        return 0.0 if pd.isna(value) else float(value)

    inv_col = "invoiced_value_fc" if "invoiced_value_fc" in df.columns else "total"
    inv_val = num(inv_col)
    prov_amt = num("provisional_freight_amount")
    act_amt = num("actual_freight_amount")
    total_cost = round(
        act_amt
        + num("lr_charges")
        + num("loading_charges")
        + num("unloading_charges")
        + num("detension_charges")
        + num("point_charges"),
        2,
    )

    return {
        "provisional_perc": round((prov_amt / inv_val) * 100, 2) if inv_val > 0 else 0.0,
        "actual_perc": round((act_amt / inv_val) * 100, 2) if inv_val > 0 else 0.0,
        "total_freight_cost": total_cost,
        "cost_per_km": round(total_cost / num("approx_distance"), 2) if num("approx_distance") > 0 else 0.0,
    }


@st.cache_data(ttl=300, show_spinner=False)
def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    """Cache Excel generation so openpyxl does not run on every rerun."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Logistics Data")
    return buffer.getvalue()


def clear_dashboard_filters(mode: str):
    filter_keys = [
        f"{mode}_filter_locations", f"{mode}_filter_pos", f"{mode}_filter_transporters", 
        f"{mode}_filter_cities", f"{mode}_filter_from_date", f"{mode}_filter_to_date", f"{mode}_filter_invoices"
    ]
    for k in filter_keys:
        st.session_state.pop(k, None)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Dynamic Unified Dashboard Renderer
# ─────────────────────────────────────────────────────────────────────────────
def render_generic_dashboard(sheet_name: str, mode: str = "purchase"):
    if mode not in DASHBOARD_CONFIGS:
        st.error(f"Invalid mode configuration context: '{mode}'")
        return

    cfg = DASHBOARD_CONFIGS[mode]
    PAGE_SIZE = 10

    st.markdown(f"## {cfg['title']}")

    if f"{mode}_editor_key_version" not in st.session_state:
        st.session_state[f"{mode}_editor_key_version"] = 0

    # Auto-Refresh Logic (300 seconds)
    if f"{mode}_last_auto_refresh" not in st.session_state:
        st.session_state[f"{mode}_last_auto_refresh"] = time.time()

    if time.time() - st.session_state[f"{mode}_last_auto_refresh"] > 300:
        if not st.session_state.get(f"{mode}_pending_edits"):
            st.session_state[f"{mode}_last_auto_refresh"] = time.time()
            st.session_state.pop(f"{mode}_working_df", None)
            st.cache_data.clear()
            st.rerun()

    # Data Initialization Context
    if f"{mode}_working_df" not in st.session_state:
        try:
            raw_df = load_worksheet_data(sheet_name)
        except Exception as e:
            st.error(f"Error loading worksheet '{sheet_name}': {e}")
            show_popup(f"Error loading worksheet '{sheet_name}': {e}", type="error")
            raw_df = pd.DataFrame()

        if raw_df.empty:
            st.warning(f"No records found in worksheet: **{sheet_name}**")
            return

        filtered = filter_by_user_location(raw_df)
        if filtered.empty:
            st.info("No records available for your assigned location(s).")
            return

        for col in cfg["default_columns"]:
            if col not in filtered.columns:
                filtered[col] = ""

        if "bill_date" in filtered.columns:
            filtered["bill_date"] = pd.to_datetime(filtered["bill_date"], errors="coerce").dt.date

        flexible_text_cols = ["po_no", "so_no", "bill_no", "challan_no", "vehicle_no", "eway_bill_no", cfg["doc_no_col"]]
        for ft_col in flexible_text_cols:
            if ft_col in filtered.columns:
                filtered[ft_col] = (
                    filtered[ft_col]
                    .fillna("")
                    .astype(str)
                    .str.replace(r"\.0$", "", regex=True)
                    .replace(["nan", "None", "<NA>", "NaN"], "")
                )

        numeric_cols = [
            "approx_distance", "provisional_freight_amount", "actual_freight_amount", "lr_charges",
            "loading_charges", "unloading_charges", "detension_charges", "point_charges", "basic"
        ]
        
        for n_col in numeric_cols:
            if n_col in filtered.columns:
                filtered[n_col] = pd.to_numeric(filtered[n_col], errors="coerce").fillna(0.0)

        filtered = calculate_freight_percentages(filtered)

        # Parse the dashboard date column once instead of on every rerun.
        date_col = cfg["doc_date_col"]
        if date_col in filtered.columns:
            parsed_dates = pd.to_datetime(
                filtered[date_col], format="%d/%m/%Y", errors="coerce"
            )
            if parsed_dates.isna().all():
                parsed_dates = pd.to_datetime(filtered[date_col], errors="coerce")
            filtered["parsed_invoice_date"] = parsed_dates

        st.session_state[f"{mode}_working_df"] = filtered.copy()
        # Fast row lookup for data-editor updates; avoids a full-column string scan per edit.
        if col_id := next((c for c in filtered.columns if c.strip().lower() == "id"), None):
            st.session_state[f"{mode}_id_to_index"] = {
                str(v).strip(): idx for idx, v in filtered[col_id].items()
            }

    if f"{mode}_pending_edits" not in st.session_state:
        st.session_state[f"{mode}_pending_edits"] = {}

    df_work = st.session_state[f"{mode}_working_df"]
    col_id = next((c for c in df_work.columns if c.strip().lower() == "id"), "id")

    # Dynamic Filter Mapping
    st.markdown("### 🔍 Filter Records")

    col_loc = next((c for c in df_work.columns if c.strip().lower() == "location_name"), None)
    col_pos = next((c for c in df_work.columns if c.strip().lower() == cfg["group_col"].lower()), None)
    col_city = next((c for c in df_work.columns if c.strip().lower() == cfg["cat_col"].lower()), None)
    col_trans = next((c for c in df_work.columns if c.strip().lower() == "transporter_name"), None)
    col_date = next((c for c in df_work.columns if c.strip().lower() == cfg["doc_date_col"].lower()), None)
    col_invoice = next((c for c in df_work.columns if c.strip().lower() == cfg["doc_no_col"].lower()), None)

    # parsed_invoice_date is prepared once when the working dataframe is loaded.
    # Avoid copying the entire dataframe on every Streamlit rerun.
    curr_df = df_work

    # Filter Section Layout
    f1, f2, f3, f4 = st.columns(4)

    with f1:
        loc_opts = sorted(curr_df[col_loc].dropna().astype(str).unique()) if col_loc else []
        selected_locations = st.multiselect("Location Name", options=loc_opts, key=f"{mode}_filter_locations")
        if col_loc and selected_locations:
            curr_df = curr_df[curr_df[col_loc].astype(str).isin(selected_locations)]

    with f2:
        pos_opts = sorted(curr_df[col_pos].dropna().astype(str).unique()) if col_pos else []
        group_label = "Group Name" if mode == "purchase" else "Place Of Supply"
        selected_pos = st.multiselect(group_label, options=pos_opts, key=f"{mode}_filter_pos")
        if col_pos and selected_pos:
            curr_df = curr_df[curr_df[col_pos].astype(str).isin(selected_pos)]

    with f3:
        trans_opts = sorted(curr_df[col_trans].dropna().astype(str).unique()) if col_trans else []
        selected_transporters = st.multiselect("Transporter Name", options=trans_opts, key=f"{mode}_filter_transporters")
        if col_trans and selected_transporters:
            curr_df = curr_df[curr_df[col_trans].astype(str).isin(selected_transporters)]

    with f4:
        city_opts = sorted(curr_df[col_city].dropna().astype(str).unique()) if col_city else []
        group_label_f4 = "Item Category" if mode == "purchase" else "Customer City"
        selected_city = st.multiselect(group_label_f4, options=city_opts, key=f"{mode}_filter_cities")
        if col_city and selected_city:
            curr_df = curr_df[curr_df[col_city].astype(str).isin(selected_city)]

    d1, d2, d3, d4 = st.columns(4)

    with d1:
        from_date = st.date_input("From Date", value=st.session_state.get(f"{mode}_filter_from_date", None), key=f"{mode}_filter_from_date")
        if from_date and "parsed_invoice_date" in curr_df:
            curr_df = curr_df[curr_df["parsed_invoice_date"].dt.date >= from_date]

    with d2:
        to_date = st.date_input("To Date", value=st.session_state.get(f"{mode}_filter_to_date", None), key=f"{mode}_filter_to_date")
        if to_date and "parsed_invoice_date" in curr_df:
            curr_df = curr_df[curr_df["parsed_invoice_date"].dt.date <= to_date]

    with d3:
        invoice_opts = sorted(curr_df[col_invoice].dropna().astype(str).unique()) if col_invoice else []
        doc_label = "GRN Document No." if mode == "purchase" else "Tax Invoice No."
        selected_invoice = st.multiselect(doc_label, options=invoice_opts, key=f"{mode}_filter_invoices")
        if col_invoice and selected_invoice:
            curr_df = curr_df[curr_df[col_invoice].astype(str).isin(selected_invoice)]

    with d4:
        st.write("")
        st.write("")
        if st.button("🔄 Refresh Data", width="stretch", type="secondary", key=f"{mode}_refresh_btn"):
            st.session_state.pop(f"{mode}_working_df", None)
            st.session_state.pop(f"{mode}_pending_edits", None)
            clear_dashboard_filters(mode)
            load_worksheet_data.clear(sheet_name)
            st.session_state[f"{mode}_last_auto_refresh"] = time.time()
            st.rerun()

    filtered_df = curr_df.copy()
    st.markdown("---")

    # Pagination Mechanics
    total_rows = len(filtered_df)
    total_pages = max(1, int(np.ceil(total_rows / PAGE_SIZE)))

    if f"{mode}_current_page" not in st.session_state:
        st.session_state[f"{mode}_current_page"] = 1

    if st.session_state[f"{mode}_current_page"] > total_pages:
        st.session_state[f"{mode}_current_page"] = total_pages

    start_idx = (st.session_state[f"{mode}_current_page"] - 1) * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, total_rows)

    # Grid Edits Intercept Logic
    editor_key = f"{mode}_data_editor_grid_{st.session_state[f'{mode}_editor_key_version']}"
    editor_state = st.session_state.get(editor_key, {})
    grid_edited_rows = editor_state.get("edited_rows", {})

    if grid_edited_rows:
        page_slice = filtered_df[cfg["default_columns"]].iloc[start_idx:end_idx]
        illegal_edit_intercepted = False

        for row_idx_str, changes in grid_edited_rows.items():
            row_idx = int(row_idx_str)
            if row_idx < len(page_slice):
                current_row = page_slice.iloc[row_idx]
                record_id = str(current_row[col_id]).strip()

                working_df = st.session_state[f"{mode}_working_df"]
                working_idx = st.session_state.get(f"{mode}_id_to_index", {}).get(record_id, current_row.name)
                working_bill = working_df.at[working_idx, "bill_no"] if "bill_no" in working_df.columns and working_idx in working_df.index else current_row.get("bill_no", "")

                pending_record = st.session_state[f"{mode}_pending_edits"].get(record_id, {})
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
                    if col_k in cfg["conditional_cols"] and not has_bill_no and float(val_v or 0.0) != 0.0:
                        blocked_cols.append(col_k.replace("_", " ").title())
                        valid_changes[col_k] = 0.0
                        illegal_edit_intercepted = True
                    else:
                        valid_changes[col_k] = val_v

                if blocked_cols:
                    st.toast(
                        f"Enter **Bill No** first before adding {', '.join(blocked_cols)} across ID {record_id}.", 
                        icon="🚫",
                        duration="long"
                    )

                if valid_changes:
                    if record_id not in st.session_state[f"{mode}_pending_edits"]:
                        st.session_state[f"{mode}_pending_edits"][record_id] = {}

                    st.session_state[f"{mode}_pending_edits"][record_id].update(valid_changes)

                    # Update only the edited row instead of recalculating the entire dataframe.
                    working_df = st.session_state[f"{mode}_working_df"]
                    for k, v in valid_changes.items():
                        working_df.at[working_idx, k] = v

                    calc_values = calculate_freight_row(working_df, working_idx)
                    for calc_col, calc_value in calc_values.items():
                        working_df.at[working_idx, calc_col] = calc_value
                        st.session_state[f"{mode}_pending_edits"][record_id][calc_col] = calc_value

        # curr_df already references the working dataframe, so no full-dataframe update is needed.
        filtered_df = curr_df

        if illegal_edit_intercepted:
            st.session_state[f"{mode}_editor_key_version"] += 1
            st.rerun()

    # Pagination Bar & Downloads
    top_col1, top_col2, top_col3 = st.columns([1, 2, 1])
    with top_col1:
        selected_top_page = st.number_input(
            "Go to Page",
            min_value=1,
            max_value=total_pages,
            value=st.session_state[f"{mode}_current_page"],
            step=1,
            key=f"{mode}_top_page_input"
        )
        if selected_top_page != st.session_state[f"{mode}_current_page"]:
            st.session_state[f"{mode}_current_page"] = selected_top_page
            st.rerun()

    with top_col2:
        st.markdown(
            f"<div style='text-align: center; margin-top: 28px; font-weight: 500; color: #555;'>"
            f"Page <b>{st.session_state[f'{mode}_current_page']}</b> of <b>{total_pages}</b> &nbsp;|&nbsp; "
            f"Showing rows <b>{start_idx + 1 if total_rows > 0 else 0}-{end_idx}</b> of <b>{total_rows}</b>"
            f"</div>",
            unsafe_allow_html=True
        )

    with top_col3:
        st.markdown("<br>", unsafe_allow_html=True)
        excel_bytes = dataframe_to_excel_bytes(filtered_df)

        st.download_button(
            label="📥 Download Filtered Data (Excel)",
            data=excel_bytes,
            file_name=cfg["export_filename"],
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="secondary",
            key=f"{mode}_download_btn"
        )

    # Column Configurations for Data Editor Grid
    # Slice to the visible 10 rows BEFORE formatting. This avoids formatting the full dataset.
    page_data = filtered_df[cfg["default_columns"]].iloc[start_idx:end_idx].copy()

    if "bill_date" in page_data.columns:
        page_data["bill_date"] = pd.to_datetime(page_data["bill_date"], errors="coerce")

    flexible_text_cols = ["po_no", "so_no", "bill_no", "challan_no", "vehicle_no", "eway_bill_no"]
    for ft_col in flexible_text_cols:
        if ft_col in page_data.columns:
            page_data[ft_col] = (
                page_data[ft_col]
                .fillna("")
                .astype(str)
                .str.replace(r"\.0$", "", regex=True)
                .replace(["nan", "None", "<NA>", "NaN"], "")
            )

    column_configuration = {
        col: st.column_config.Column(
            label=col.replace("_", " ").title(), disabled=True
        )
        for col in page_data.columns
    }

    transporter_options = st.secrets.get("connections", {}).get("gsheets", {}).get("transporter_list", [])

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
            "Challan No", disabled=False, default="",
            help="Accepts numbers, letters, or mixed alphanumeric characters"
        ),
        "vehicle_type": st.column_config.SelectboxColumn(
            "Vehicle Type",
            options=[
                "clubbed", "By Hand", "Courier", "Part Load", "Tata Ace / Pickup 9 ft",
                "14 feet", "17 feet", "19/20 feet", "22/24 feet", "32 SXL 7 ton",
                "32 SXL 9 ton", "SRT/ Internal Movement"
            ],
            required=False, disabled=False
        ),
        "transporter_name": st.column_config.SelectboxColumn(
            "Transporter Name",
            options=transporter_options,
            required=False, disabled=False
        ),
        "approx_distance": st.column_config.NumberColumn(
            "Approx Distance", format="%d", disabled=False
        ),
        "provisional_freight_amount": st.column_config.NumberColumn(
            "Provisional Freight Amt", format="%.2f", disabled=False
        ),
        "actual_freight_amount": st.column_config.NumberColumn(
            "Actual Freight Amt", format="%.2f", disabled=False,
            help="Requires Bill No first before editing non-zero amounts"
        ),
        "unloading_charges": st.column_config.NumberColumn(
            "Unloading Charges", format="%.2f", disabled=False,
            help="Requires Bill No first before editing non-zero amounts"
        ),
        "detension_charges": st.column_config.NumberColumn(
            "Detention Charges/ ODA", format="%.2f", disabled=False,
            help="Requires Bill No first before editing non-zero amounts"
        ),
        "point_charges": st.column_config.NumberColumn(
            "Point Charges", format="%.2f", disabled=False,
            help="Requires Bill No first before editing non-zero amounts"
        ),
        "lr_charges": st.column_config.NumberColumn(
            "LR Charges", format="%.2f", disabled=False
        ),
        "loading_charges": st.column_config.NumberColumn(
            "Loading Charges", format="%.2f", disabled=False
        ),
        "po_no": st.column_config.TextColumn(
            "PO No", disabled=False, default="",
            help="Accepts numbers, letters, or mixed alphanumeric characters"
        ),
        "so_no": st.column_config.TextColumn(
            "SO No", disabled=False, default="",
            help="Accepts numbers, letters, or mixed alphanumeric characters"
        ),
        "bill_no": st.column_config.TextColumn(
            "Bill No", disabled=False, default="",
            help="Accepts numbers, letters, or mixed alphanumeric characters"
        ),
        "bill_date": st.column_config.DateColumn(
            "Bill Date", format="YYYY-MM-DD", disabled=False
        ),
        "bill_receiving_status": st.column_config.SelectboxColumn(
            "Bill Receiving Status",
            options=["Received", "Pending", "Rejected"],
            required=False, disabled=False
        ),
        "remark": st.column_config.TextColumn("Remark", disabled=False),
    })

    st.markdown("##### ✏️ Double click any cell below to edit value:")

    st.data_editor(
        page_data,
        column_config=column_configuration,
        width="stretch",
        hide_index=True,
        key=f"{mode}_data_editor_grid_{st.session_state[f'{mode}_editor_key_version']}"
    )

    # Atomic Persistence Handlers
    if st.session_state[f"{mode}_pending_edits"]:
        st.info(f"📝 You have unsaved changes for **{len(st.session_state[f'{mode}_pending_edits'])}** record(s).")
        if st.button("💾 Save Changes to Database", type="primary", key=f"{mode}_save_btn"):
            success = update_gsheet_atomic(
                sheet_name=sheet_name,
                id_col=col_id,
                pending_edits=st.session_state[f"{mode}_pending_edits"],
                required_columns=cfg["default_columns"]
            )
            if success:
                st.session_state[f"{mode}_pending_edits"] = {}
                st.session_state[f"{mode}_last_save_message"] = "Changes saved to Database successfully!"
                # Keep the edited dataframe in memory. Do not reload the whole Google Sheet.
                st.success("✅ Changes saved to Database successfully!")


# Convenience Dynamic Wrappers
def render_purchase_dashboard(sheet_name: str):
    render_generic_dashboard(sheet_name=sheet_name, mode="purchase")

def render_sales_dashboard(sheet_name: str):
    render_generic_dashboard(sheet_name=sheet_name, mode="sales")
