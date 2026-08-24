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

def process_and_upload_excel(uploaded_file, target_sheet_name: str):
    try:
        # 1. Read Excel file into Pandas DataFrame
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

        # Ensure all selected columns exist in the incoming dataframe (fill missing with empty strings)
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

        # Convert dates and drop invalid/empty date rows (e.g., summary totals)
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

        # ── 2. Add Unique ID, Processed Month, and Date Strings ──────────────
        # Insert unique ID as the first column
        df.insert(0, "id", [uuid.uuid4().hex for _ in range(len(df))])

        df["month"] = parsed_dates.dt.strftime("%b-%y")
        df["invoice_doc_date"] = parsed_dates.dt.strftime("%d/%m/%Y")
        
        # Replace NaN / NaT values for JSON safety
        df = df.replace({np.nan: None})

        # ── 3. Connect to Google Sheets & Target Worksheet ──────────────────
        spreadsheet = connect_gsheet()

        try:
            worksheet = spreadsheet.worksheet(target_sheet_name)
            existing_data = worksheet.get_all_records()
        except gspread.exceptions.WorksheetNotFound:
            # Create sheet automatically if missing
            worksheet = spreadsheet.add_worksheet(title=target_sheet_name, rows=1000, cols=len(df.columns) + 2)
            existing_data = []

        # Convert new dataframe rows to list format
        new_rows = df.where(df.notnull(), "").values.tolist()

        # ── 4. Direct Append Logic ───────────────────────────────────────────
        if existing_data:
            # Sheet already has headers/data -> simply append new rows
            worksheet.append_rows(new_rows, value_input_option="USER_ENTERED")
        else:
            # Empty or newly created sheet -> write headers first, then append rows
            headers = df.columns.tolist()
            worksheet.clear()
            worksheet.update([headers] + new_rows)

        uploaded_months = df["month"].dropna().unique().tolist()
        show_popup(f"Successfully appended {len(df)} records for month(s): {', '.join(uploaded_months)}!", type="success")

        print(f"Successfully appended {len(df)} records for month(s): {', '.join(uploaded_months)}!")
        st.success(f"Successfully appended {len(df)} records for month(s): {', '.join(uploaded_months)}!")
        return True

    except Exception as e:
        show_popup(f"Error processing file upload: {str(e)}", type="error")
        print(f"Upload Error: {e}")
        return False




# Unique id column not added   
# def process_and_upload_excel(uploaded_file, target_sheet_name: str):

#     try:
#         # 1. Read Excel file into Pandas DataFrame
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
#             "challan_no", "vehicle_no", "vehicle_type", "approx_distance", "eway_bill_no"]

#         if "invoice_doc_date" not in df.columns:
#             show_popup("Excel file missing required column: 'invoice_doc_date'", type="error")
#             return False

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
#         df["location_name"] = df["location_name"].str.strip()
#         # ── 2. Add Processed Month and Date Strings ──────────────────────────
#         df["month"] = parsed_dates.dt.strftime("%b-%y")
#         df["invoice_doc_date"] = parsed_dates.dt.strftime("%d/%m/%Y")
        
#         # Replace NaN / NaT values for JSON safety
#         df = df.replace({np.nan: None})

#         # ── 3. Connect to Google Sheets & Target Worksheet ──────────────────
#         spreadsheet = connect_gsheet()

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
