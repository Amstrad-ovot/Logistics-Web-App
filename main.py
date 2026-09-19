import io
import time
import uuid
import gspread
import numpy as np
import pandas as pd
import streamlit as st
from datetime import date
from gspread.utils import rowcol_to_a1
from google.auth.transport.requests import Request
from datetime import datetime, timedelta,date
from google.oauth2.service_account import Credentials



# ──────────────────────────────────────────────
# Connection & UI Layer (Fixed Connection Drops)
# ──────────────────────────────────────────────

def get_gsheet_conn():
    creds_dict = {
        "type": st.secrets["connections"]["gsheets"]["type"],
        "project_id": st.secrets["connections"]["gsheets"]["project_id"],
        "private_key_id": st.secrets["connections"]["gsheets"]["private_key_id"],
        "private_key": st.secrets["connections"]["gsheets"]["private_key"],
        "client_email": st.secrets["connections"]["gsheets"]["client_email"],
        "client_id": st.secrets["connections"]["gsheets"]["client_id"],
        "auth_uri": st.secrets["connections"]["gsheets"]["auth_uri"],
        "token_uri": st.secrets["connections"]["gsheets"]["token_uri"],
    }

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    
    # Refresh credentials proactively to prevent expired token drops
    if creds.expired:
        creds.refresh(Request())

    # Pass authorized credentials with optimized timeout configuration
    client = gspread.authorize(creds)
    return client


def connect_gsheet():
    """Connects to Google Sheets with retry mechanism to prevent WinError 10054 drops."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            client = get_gsheet_conn()
            SPREADSHEET_ID = st.secrets["connections"]["gsheets"]["spreadsheet_id"]
            spreadsheet = client.open_by_key(SPREADSHEET_ID)
            return spreadsheet
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Unable to connect google sheet after {max_retries} attempts: {e}")
                show_popup(f"Connection lost. Please check your internet or retry.", type="error")
                raise e
            time.sleep(2)  # Wait 2 seconds before retrying socket connection


def show_popup(message, type="success"):
    if type == "success":
        st.toast(f"✅ {message}")
    elif type == "error":
        st.toast(f"❌ {message}")
    elif type == "warning":
        st.toast(f"⚠️ {message}")
    elif type == "info":
        st.toast(f"ℹ️ {message}")


# ──────────────────────────────────────────────
# Data Ingestion & Month Replacement Layer
# ──────────────────────────────────────────────

def process_and_upload_excel(uploaded_file, target_sheet_name: str, custom_sheet: str):
    try:
        # ── 1. Read Excel file into Pandas DataFrame ─────────────────────────
        df = pd.read_excel(uploaded_file)

        # Standardize column names
        df.columns = [
            str(col)
            .strip()
            .lower()
            .replace(" ", "_")
            .replace(".", "")
            .replace("(", "")
            .replace(")", "")
            for col in df.columns
        ]

        selected_columns = [
            "location_name",
            "invoice_type_desc",
            "invoice_doc_date",
            "tax_invoice_no",
            "customer_name",
            "place_of_supply",
            "fg_qty",
            "selling_fc_value",
            "igst",
            "cgst",
            "sgst",
            "invoiced_value_fc",
            "transporter_name",
            "challan_no",
            "vehicle_no",
            "vehicle_type",
            "approx_distance",
            "eway_bill_no",
        ]

        # Ensure all selected columns exist in incoming dataframe
        for col in selected_columns:
            if col not in df.columns:
                if col == "invoice_doc_date":
                    show_popup(
                        "Excel file missing required column: 'invoice_doc_date'",
                        type="error",
                    )
                    return False
                df[col] = ""

        # ── EXCLUDE SUMMARY / COUNT ROWS ─────────────────────────────────────
        first_col = df.columns[0]
        count_mask = (
            df[first_col].astype(str).str.lower().str.contains("count", na=False)
        )
        df = df[~count_mask]

        # Convert dates and drop invalid/empty date rows
        parsed_dates = pd.to_datetime(
            df["invoice_doc_date"], format="%d/%m/%Y", errors="coerce"
        )

        if parsed_dates.isna().all():
            parsed_dates = pd.to_datetime(
                df["invoice_doc_date"], errors="coerce"
            )

        valid_date_mask = parsed_dates.notna()
        df = df[valid_date_mask].copy()

        if df.empty:
            show_popup(
                "No valid transaction rows found after dropping count/summary rows.",
                type="error",
            )
            return False

        # Filter to selected target columns & format tax_invoice_no
        df = df[selected_columns]
        df["tax_invoice_no"] = df["tax_invoice_no"].astype(str).str.strip()

        # ── GROUPBY AGGREGATION ──────────────────────────────────────────────
        numeric_cols = [
            "fg_qty",
            "selling_fc_value",
            "igst",
            "cgst",
            "sgst",
            "invoiced_value_fc",
        ]
        string_cols = [
            "location_name",
            "invoice_type_desc",
            "invoice_doc_date",
            "customer_name",
            "place_of_supply",
            "transporter_name",
            "challan_no",
            "vehicle_no",
            "vehicle_type",
            "approx_distance",
            "eway_bill_no",
        ]

        # Ensure numeric columns are coerced properly before summing
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

        # Helper function to join distinct non-null string values with comma
        def unique_join(series):
            unique_vals = [
                str(v).strip()
                for v in series.dropna().unique()
                if str(v).strip() != ""
            ]
            return ", ".join(unique_vals)

        # Build aggregation mapping
        agg_dict = {col: "sum" for col in numeric_cols}
        agg_dict.update({col: unique_join for col in string_cols})

        # Apply GroupBy on tax_invoice_no
        df = df.groupby("tax_invoice_no", as_index=False).agg(agg_dict)

        # Re-parse invoice_doc_date post aggregation (take the first date if multiple exist)
        df["invoice_doc_date"] = df["invoice_doc_date"].apply(
            lambda x: x.split(",")[0].strip() if x else ""
        )
        parsed_dates = pd.to_datetime(
            df["invoice_doc_date"], format="%d/%m/%Y", errors="coerce"
        )
        if parsed_dates.isna().all():
            parsed_dates = pd.to_datetime(
                df["invoice_doc_date"], errors="coerce"
            )

        # ── MAP CUST_CITY_NAME FROM CUSTOMIZED SHEET ────────────────────────
        spreadsheet = connect_gsheet()

        try:
            custom_ws = spreadsheet.worksheet(custom_sheet)
            custom_records = custom_ws.get_all_records()

            if custom_records:
                custom_df = pd.DataFrame(custom_records)
                custom_df.columns = [
                    str(c).strip().lower() for c in custom_df.columns
                ]

                if (
                    "commercial_invoice_no" in custom_df.columns
                    and "cust_city_name" in custom_df.columns
                ):
                    custom_df["commercial_invoice_no"] = (
                        custom_df["commercial_invoice_no"]
                        .astype(str)
                        .str.strip()
                        .str.upper()
                    )
                    df["tax_invoice_lookup"] = (
                        df["tax_invoice_no"]
                        .astype(str)
                        .str.strip()
                        .str.upper()
                    )

                    city_map_df = custom_df[
                        custom_df["cust_city_name"].astype(str).str.strip()
                        != ""
                    ].drop_duplicates(subset=["commercial_invoice_no"])
                    city_map = dict(
                        zip(
                            city_map_df["commercial_invoice_no"],
                            city_map_df["cust_city_name"],
                        )
                    )

                    df["cust_city_name"] = (
                        df["tax_invoice_lookup"].map(city_map).fillna("")
                    )
                    df.drop(columns=["tax_invoice_lookup"], inplace=True)
                else:
                    df["cust_city_name"] = ""
            else:
                df["cust_city_name"] = ""
        except Exception as e:
            print(f"Warning: Could not fetch city map from '{custom_sheet}': {e}")
            df["cust_city_name"] = ""

        # ── 2. Add Unique ID, Processed Month, and Formatting ────────────────
        df.insert(0, "id", [uuid.uuid4().hex for _ in range(len(df))])
        print("Today date is: ",date.today())
        df["month"] = parsed_dates.dt.strftime("%b-%y")
        df["created_date"] = date.today().strftime("%Y-%m-%d")  
        df["invoice_doc_date"] = parsed_dates.dt.strftime("%d/%m/%Y")

        # ── 3. Target Worksheet Setup & Deduplication Check ──────────────────
        try:
            worksheet = spreadsheet.worksheet(target_sheet_name)
            all_rows = worksheet.get_all_values()
        except gspread.exceptions.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(
                title=target_sheet_name, rows=1000, cols=len(df.columns) + 2
            )
            all_rows = []

        existing_invoices = set()

        if len(all_rows) > 1:
            headers_lower = [str(h).strip().lower() for h in all_rows[0]]
            if "tax_invoice_no" in headers_lower:
                tax_inv_col_idx = headers_lower.index("tax_invoice_no")
                existing_invoices = {
                    str(row[tax_inv_col_idx]).strip().upper()
                    for row in all_rows[1:]
                    if len(row) > tax_inv_col_idx
                    and str(row[tax_inv_col_idx]).strip()
                }

        # ── Filter out duplicate tax_invoice_no rows ──────────────────────────
        df["_invoice_check"] = (
            df["tax_invoice_no"].astype(str).str.strip().str.upper()
        )
        initial_count = len(df)

        df = df[~df["_invoice_check"].isin(existing_invoices)].copy()
        df.drop(columns=["_invoice_check"], inplace=True)

        skipped_count = initial_count - len(df)

        if df.empty:
            msg = f"All {skipped_count} record(s) in this file already exist in '{target_sheet_name}'. No new records added."
            show_popup(msg, type="warning")
            st.warning(msg)
            return True

        # Clean NaN/NaT values for JSON compatibility
        df = df.replace({np.nan: None})
        new_rows = df.where(df.notnull(), "").values.tolist()

        # ── 4. Write Data Back to Google Sheet ────────────────────────────────
        if len(all_rows) > 0:
            worksheet.append_rows(new_rows, value_input_option="USER_ENTERED")
        else:
            headers = df.columns.tolist()
            worksheet.clear()
            worksheet.update(
                [headers] + new_rows, value_input_option="USER_ENTERED"
            )

        st.cache_data.clear()

        uploaded_months = df["month"].dropna().unique().tolist()
        success_msg = f"Successfully appended {len(df)} new record(s) for month(s): {', '.join(uploaded_months)}!"
        if skipped_count > 0:
            success_msg += (
                f" (Skipped {skipped_count} existing duplicate invoice(s))."
            )

        show_popup(success_msg, type="success")
        st.success(success_msg)
        return True

    except Exception as e:
        show_popup(f"Error processing file upload: {str(e)}", type="error")
        print(f"Upload Error: {e}")
        return False


def upload_customised_report(uploaded_customised_file, target_sheet_name):
    try:
        custom_data = pd.read_excel(uploaded_customised_file)
        
        # Clean column names
        custom_data.columns = (
            custom_data.columns.str.lower()
            .str.strip()
            .str.replace(" ", "_")
            .str.replace(".", "", regex=False)
        )

        # ── EXCLUDE SUMMARY / COUNT ROWS ─────────────────────────────────────
        first_col = custom_data.columns[0]
        count_mask = custom_data[first_col].astype(str).str.lower().str.contains("count", na=False)
        custom_data = custom_data[~count_mask]
        
        selected_cols = ["customer_name", "commercial_invoice_no", "item_code", "group_name", "cust_city_name"] 
        custom_data = custom_data[[col for col in selected_cols if col in custom_data.columns]]

        # Helper to join unique, non-null values with commas
        def join_unique(series):
            unique_vals = series.dropna().astype(str).str.strip().unique()
            unique_vals = [val for val in unique_vals if val != ""]
            return ", ".join(unique_vals)

        # Group by commercial_invoice_no and aggregate other columns
        grouped_df = (
            custom_data.groupby("commercial_invoice_no", as_index=False)
            .agg(join_unique)
        )

        # ── Replace / Populate Target Worksheet ───────────────────
        spreadsheet = connect_gsheet()
        
        try:
            worksheet = spreadsheet.worksheet(target_sheet_name)
            worksheet.clear()
        except Exception:
            rows_needed = str(max(len(grouped_df) + 10, 100))
            cols_needed = str(max(len(grouped_df.columns) + 2, 10))
            worksheet = spreadsheet.add_worksheet(title=target_sheet_name, rows=rows_needed, cols=cols_needed)

        prepared_df = grouped_df.fillna("")
        data_to_write = [prepared_df.columns.tolist()] + prepared_df.astype(str).values.tolist()

        worksheet.update("A1", data_to_write)

        st.cache_data.clear()
        show_popup(f"Successfully overwritten '{target_sheet_name}'!", type="success")
        return True  # Return True instead of grouped_df

    except Exception as e:
        print(f"Error in upload_customised_report function: {e}")
        show_popup(f"Error in upload_customised_report function: {e}", type="error")
        return False  # Return False instead of None


def missing_updates(
    from_date=None,
    to_date=None,
    filter_locations=None,
    target_sheet_name: str = "TargetSheet",
):

  try:
    # 1. Fetch data from Google Sheet
    spreadsheet = connect_gsheet()
    worksheet = spreadsheet.worksheet(target_sheet_name)
    records = worksheet.get_all_records()

    if not records:
      show_popup("No records found in the sheet.", type="warning")
      return pd.DataFrame()

    df = pd.DataFrame(records)

    # Standardize column names
    df.columns = [str(col).strip().lower() for col in df.columns]

    # Ensure required columns exist
    required_cols = ["created_date", "location_name"]
    for col in required_cols:
      if col not in df.columns:
        show_popup(f"Missing required column: '{col}'", type="error")
        return pd.DataFrame()

    # 2. Parse dates safely
    df["created_date_parsed"] = pd.to_datetime(
        df["created_date"], errors="coerce"
    ).dt.date

    parsed_from = pd.to_datetime(from_date).date() if from_date else None
    parsed_to = pd.to_datetime(to_date).date() if to_date else None

    # 3. Apply Optional Date Range Filter
    if parsed_from and parsed_to:
      df = df[
          (df["created_date_parsed"] >= parsed_from)
          & (df["created_date_parsed"] <= parsed_to)
      ]
    elif parsed_from:
      df = df[df["created_date_parsed"] >= parsed_from]
    elif parsed_to:
      df = df[df["created_date_parsed"] <= parsed_to]

    # 4. Apply Optional Location Filter (Multiple locations support)
    if filter_locations:
      if isinstance(filter_locations, str):
        filter_locations = [filter_locations]

      clean_locations = [
          str(loc).strip().lower()
          for loc in filter_locations
          if str(loc).strip()
      ]
      if clean_locations:
        df = df[
            df["location_name"]
            .astype(str)
            .str.strip()
            .str.lower()
            .isin(clean_locations)
        ]

    # 5. Filter for records created at least 3 days prior to today
    cutoff_date = date.today() - timedelta(days=4)
    df = df[df["created_date_parsed"] <= cutoff_date]

    # 6. Editable columns list (Fixed missing comma after "bill_receiving_status")
    editable_columns = [
        "provisional_freight_amount",
        "lr_charges",
        "loading_charges",
        "unloading_charges",
        "detension_charges",
        "point_charges",
        "po_no",
        "bill_no",
        "bill_date",
        "bill_receiving_status",
        "remark",
    ]

    # Check which editable columns exist in the DataFrame
    existing_editable_cols = [c for c in editable_columns if c in df.columns]

    if not existing_editable_cols:
      show_popup(
          "No editable columns found in dataset to evaluate missing updates.",
          type="warning",
      )
      return pd.DataFrame()

    # 7. Create a boolean mask where ALL editable columns must be empty or 0
    # Start with True for all rows
    missing_mask = pd.Series(True, index=df.index)

    for col in existing_editable_cols:
      # Convert series to clean strings for standard comparison
      str_val = df[col].astype(str).str.strip().str.lower()

      # Column is considered empty if it is NaN, empty string, 'none', 'nan', '0', or '0.0'
      col_empty_or_zero = (
          df[col].isna()
          | (str_val == "")
          | (str_val == "none")
          | (str_val == "nan")
          | (str_val == "0")
          | (str_val == "0.0")
          | (df[col] == 0)
      )

      # Accumulate using AND logic (&): every editable field must be empty or 0
      missing_mask = missing_mask & col_empty_or_zero

    # Filter dataset for records where ALL editable fields are missing/unupdated
    missing_updates_df = df[missing_mask].copy()

    # Cleanup temporary helper column
    missing_updates_df.drop(
        columns=["created_date_parsed"], inplace=True, errors="ignore"
    )

    if missing_updates_df.empty:
      show_popup(
          "No missing updates found matching your criteria.", type="info"
      )

    return missing_updates_df

  except Exception as e:
    print(f"Error in missing_updates function: {e}")
    show_popup(f"Error in missing_updates function: {e}", type="error")
    return pd.DataFrame()
