import io
import time
import uuid
import gspread
import numpy as np
import pandas as pd
import streamlit as st
from google.auth.transport.requests import Request
from gspread.utils import rowcol_to_a1
from datetime import datetime, timedelta
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
            str(col).strip().lower().replace(" ", "_").replace(".", "").replace("(", "").replace(")", "")
            for col in df.columns
        ]

        selected_columns = [
            "location_name", "invoice_type_desc", "invoice_doc_date", "tax_invoice_no",
            "customer_name", "place_of_supply", "fg_qty", "selling_fc_value",
            "igst", "cgst", "sgst", "invoiced_value_fc", "transporter_name",
            "challan_no", "vehicle_no", "vehicle_type", "approx_distance", "eway_bill_no"
        ]

        # Ensure all selected columns exist in incoming dataframe
        for col in selected_columns:
            if col not in df.columns:
                if col == "invoice_doc_date":
                    show_popup("Excel file missing required column: 'invoice_doc_date'", type="error")
                    return False
                df[col] = ""

        # ── EXCLUDE SUMMARY / COUNT ROWS ─────────────────────────────────────
        first_col = df.columns[0]
        count_mask = df[first_col].astype(str).str.lower().str.contains("count", na=False)
        df = df[~count_mask]

        # Convert dates and drop invalid/empty date rows
        parsed_dates = pd.to_datetime(df["invoice_doc_date"], format="%d/%m/%Y", errors="coerce")

        if parsed_dates.isna().all():
            parsed_dates = pd.to_datetime(df["invoice_doc_date"], errors="coerce")

        valid_date_mask = parsed_dates.notna()
        df = df[valid_date_mask].copy()
        parsed_dates = parsed_dates[valid_date_mask]

        if df.empty:
            show_popup("No valid transaction rows found after dropping count/summary rows.", type="error")
            return False

        # Filter to selected target columns
        df = df[selected_columns]
        df["location_name"] = df["location_name"].astype(str).str.strip()

        # ── MAP CUST_CITY_NAME FROM CUSTOMIZED SHEET ────────────────────────
        spreadsheet = connect_gsheet()
        
        try:
            custom_ws = spreadsheet.worksheet(custom_sheet)
            custom_records = custom_ws.get_all_records()
            
            if custom_records:
                custom_df = pd.DataFrame(custom_records)
                custom_df.columns = [str(c).strip().lower() for c in custom_df.columns]
                
                if "commercial_invoice_no" in custom_df.columns and "cust_city_name" in custom_df.columns:
                    custom_df["commercial_invoice_no"] = custom_df["commercial_invoice_no"].astype(str).str.strip().str.upper()
                    df["tax_invoice_lookup"] = df["tax_invoice_no"].astype(str).str.strip().str.upper()
                    
                    city_map_df = (
                        custom_df[custom_df["cust_city_name"].astype(str).str.strip() != ""]
                        .drop_duplicates(subset=["commercial_invoice_no"])
                    )
                    city_map = dict(zip(city_map_df["commercial_invoice_no"], city_map_df["cust_city_name"]))
                    
                    df["cust_city_name"] = df["tax_invoice_lookup"].map(city_map).fillna("")
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
        df["month"] = parsed_dates.dt.strftime("%b-%y")
        df["invoice_doc_date"] = parsed_dates.dt.strftime("%d/%m/%Y")

        # ── 3. Target Worksheet Setup & Deduplication Check ──────────────────
        try:
            worksheet = spreadsheet.worksheet(target_sheet_name)
            all_rows = worksheet.get_all_values()
        except gspread.exceptions.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=target_sheet_name, rows=1000, cols=len(df.columns) + 2)
            all_rows = []

        existing_invoices = set()

        if len(all_rows) > 1:
            # First row contains headers
            headers_lower = [str(h).strip().lower() for h in all_rows[0]]
            if "tax_invoice_no" in headers_lower:
                tax_inv_col_idx = headers_lower.index("tax_invoice_no")
                # Extract all existing invoice numbers from database
                existing_invoices = {
                    str(row[tax_inv_col_idx]).strip().upper()
                    for row in all_rows[1:]
                    if len(row) > tax_inv_col_idx and str(row[tax_inv_col_idx]).strip()
                }

        # ── Filter out duplicate tax_invoice_no rows ──────────────────────────
        df["_invoice_check"] = df["tax_invoice_no"].astype(str).str.strip().str.upper()
        initial_count = len(df)
        
        # Keep only rows whose tax_invoice_no is NOT in existing_invoices
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
            worksheet.update([headers] + new_rows, value_input_option="USER_ENTERED")

        # Clear cached DataFrames to show updated database immediately
        st.cache_data.clear()

        uploaded_months = df["month"].dropna().unique().tolist()
        success_msg = f"Successfully appended {len(df)} new record(s) for month(s): {', '.join(uploaded_months)}!"
        if skipped_count > 0:
            success_msg += f" (Skipped {skipped_count} existing duplicate invoice(s))."

        show_popup(success_msg, type="success")
        st.success(success_msg)
        return True

    except Exception as e:
        show_popup(f"Error processing file upload: {str(e)}", type="error")
        print(f"Upload Error: {e}")
        return False


# def process_and_upload_excel(uploaded_file, target_sheet_name: str, custom_sheet: str):
#     try:
#         # ── 1. Read Excel file into Pandas DataFrame ─────────────────────────
#         df = pd.read_excel(uploaded_file)

#         # Standardize column names
#         df.columns = [
#             str(col).strip().lower().replace(" ", "_").replace(".", "").replace("(", "").replace(")", "")
#             for col in df.columns
#         ]

#         selected_columns = [
#             "location_name", "invoice_type_desc", "invoice_doc_date", "tax_invoice_no",
#             "customer_name", "place_of_supply", "fg_qty", "selling_fc_value",
#             "igst", "cgst", "sgst", "invoiced_value_fc", "transporter_name",
#             "challan_no", "vehicle_no", "vehicle_type", "approx_distance", "eway_bill_no"
#         ]

#         # Ensure all selected columns exist in incoming dataframe (fill missing with empty strings)
#         for col in selected_columns:
#             if col not in df.columns:
#                 if col == "invoice_doc_date":
#                     show_popup("Excel file missing required column: 'invoice_doc_date'", type="error")
#                     return False
#                 df[col] = ""

#         # ── EXCLUDE SUMMARY / COUNT ROWS ─────────────────────────────────────
#         first_col = df.columns[0]
#         count_mask = df[first_col].astype(str).str.lower().str.contains("count", na=False)
#         df = df[~count_mask]

#         # Convert dates and drop invalid/empty date rows (e.g., summary totals)
#         parsed_dates = pd.to_datetime(df["invoice_doc_date"], format="%d/%m/%Y", errors="coerce")

#         if parsed_dates.isna().all():
#             parsed_dates = pd.to_datetime(df["invoice_doc_date"], errors="coerce")

#         valid_date_mask = parsed_dates.notna()
#         df = df[valid_date_mask].copy()
#         parsed_dates = parsed_dates[valid_date_mask]

#         if df.empty:
#             show_popup("No valid transaction rows found after dropping count/summary rows.", type="error")
#             return False

#         # Filter to selected target columns
#         df = df[selected_columns]
#         df["location_name"] = df["location_name"].astype(str).str.strip()

#         # ── MAP CUST_CITY_NAME FROM CUSTOMIZED SHEET ────────────────────────
#         spreadsheet = connect_gsheet()
        
#         try:
#             custom_ws = spreadsheet.worksheet(custom_sheet)
#             custom_records = custom_ws.get_all_records()
            
#             if custom_records:
#                 custom_df = pd.DataFrame(custom_records)
#                 # Standardize column names for safe merging
#                 custom_df.columns = [str(c).strip().lower() for c in custom_df.columns]
                
#                 if "commercial_invoice_no" in custom_df.columns and "cust_city_name" in custom_df.columns:
#                     # Clean lookup values
#                     custom_df["commercial_invoice_no"] = custom_df["commercial_invoice_no"].astype(str).str.strip().str.upper()
#                     df["tax_invoice_lookup"] = df["tax_invoice_no"].astype(str).str.strip().str.upper()
                    
#                     # Keep deduplicated map (first non-empty city per invoice)
#                     city_map_df = (
#                         custom_df[custom_df["cust_city_name"].astype(str).str.strip() != ""]
#                         .drop_duplicates(subset=["commercial_invoice_no"])
#                     )
#                     city_map = dict(zip(city_map_df["commercial_invoice_no"], city_map_df["cust_city_name"]))
                    
#                     # Map to cust_city_name column
#                     df["cust_city_name"] = df["tax_invoice_lookup"].map(city_map).fillna("")
#                     df.drop(columns=["tax_invoice_lookup"], inplace=True)
#                 else:
#                     df["cust_city_name"] = ""
#             else:
#                 df["cust_city_name"] = ""
#         except Exception as e:
#             print(f"Warning: Could not fetch city map from '{custom_sheet}': {e}")
#             df["cust_city_name"] = ""

#         # ── 2. Add Unique ID, Processed Month, and Date Strings ──────────────
#         # Insert unique ID as the first column
#         df.insert(0, "id", [uuid.uuid4().hex for _ in range(len(df))])

#         df["month"] = parsed_dates.dt.strftime("%b-%y")
#         df["invoice_doc_date"] = parsed_dates.dt.strftime("%d/%m/%Y")
        
#         # Replace NaN / NaT values for JSON safety
#         df = df.replace({np.nan: None})

#         # ── 3. Target Worksheet Setup ────────────────────────────────────────
#         try:
#             worksheet = spreadsheet.worksheet(target_sheet_name)
#             existing_data = worksheet.get_all_records()
#         except gspread.exceptions.WorksheetNotFound:
#             # Create sheet automatically if missing
#             worksheet = spreadsheet.add_worksheet(title=target_sheet_name, rows=1000, cols=len(df.columns) + 2)
#             existing_data = []

#         # Convert new dataframe rows to list format
#         new_rows = df.where(df.notnull(), "").values.tolist()

#         # ── 4. Direct Append Logic ───────────────────────────────────────────
#         if existing_data:
#             # Sheet already has headers/data -> simply append new rows
#             worksheet.append_rows(new_rows, value_input_option="USER_ENTERED")
#         else:
#             # Empty or newly created sheet -> write headers first, then append rows
#             headers = df.columns.tolist()
#             worksheet.clear()
#             worksheet.update([headers] + new_rows)

#         uploaded_months = df["month"].dropna().unique().tolist()
#         show_popup(f"Successfully appended {len(df)} records for month(s): {', '.join(uploaded_months)}!", type="success")

#         print(f"Successfully appended {len(df)} records for month(s): {', '.join(uploaded_months)}!")
#         st.success(f"Successfully appended {len(df)} records for month(s): {', '.join(uploaded_months)}!")
#         return True

#     except Exception as e:
#         show_popup(f"Error processing file upload: {str(e)}", type="error")
#         print(f"Upload Error: {e}")
#         return False



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
        return True  # 👈 Return True instead of grouped_df

    except Exception as e:
        print(f"Error in upload_customised_report function: {e}")
        show_popup(f"Error in upload_customised_report function: {e}", type="error")
        return False  # 👈 Return False instead of None
    
