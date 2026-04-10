"""Test nhanh kết nối Google Sheets + Drive"""
import os
import time
from dotenv import load_dotenv

load_dotenv()

print("=== TEST KẾT NỐI GOOGLE API ===\n")

# 1. Test token
print("[1] Đọc token...")
t = time.time()
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_authorized_user_file('config/token.json', SCOPES)
if creds.expired and creds.refresh_token:
    creds.refresh(Request())
print(f"    OK ({time.time()-t:.1f}s) — Token valid: {creds.valid}")

# 2. Test Sheets
print("[2] Kết nối Google Sheets...")
t = time.time()
import gspread
gc = gspread.authorize(creds)
sheet_id = os.getenv("SPREADSHEET_ID")
print(f"    Sheet ID: {sheet_id}")
ws = gc.open_by_key(sheet_id).sheet1
print(f"    OK ({time.time()-t:.1f}s) — Rows: {ws.row_count}")

# 3. Test đọc header
print("[3] Đọc header...")
t = time.time()
headers = ws.row_values(1)
print(f"    OK ({time.time()-t:.1f}s) — Headers: {headers}")

# 4. Test Drive
print("[4] Kết nối Google Drive...")
t = time.time()
from googleapiclient.discovery import build
drive = build('drive', 'v3', credentials=creds)
about = drive.about().get(fields="user").execute()
print(f"    OK ({time.time()-t:.1f}s) — User: {about['user']['emailAddress']}")

print("\n✅ TẤT CẢ KẾT NỐI OK!")
