import os
import io
import gspread
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from dotenv import load_dotenv

load_dotenv()

# Cấu hình chứng thực
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
TOKEN_FILE = 'config/token.json'
CLIENT_SECRET_FILE = 'config/client_secret.json'

class GoogleManager:
    def __init__(self):
        creds = None
        if os.path.exists(TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(CLIENT_SECRET_FILE):
                    raise FileNotFoundError(f"Chưa tìm thấy {CLIENT_SECRET_FILE}. Hãy tải từ Google Cloud Console về!")
                flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
            # Lưu lại token cứng để lần sau không cần đăng nhập lại nữa
            with open(TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())
                
        self.creds = creds
        # Google Sheets Client
        self.gs_client = gspread.authorize(self.creds)
        self.sheet_id = os.getenv("SPREADSHEET_ID")
        if not self.sheet_id:
            raise ValueError("Thiếu biến SPREADSHEET_ID ở file .env")
            
        print("Đang kết nối vào Google Sheets...")
        self.worksheet = self.gs_client.open_by_key(self.sheet_id).sheet1
        
        # Google Drive Client
        print("Đang kết nối vào Google Drive...")
        self.drive_service = build('drive', 'v3', credentials=self.creds)

    def get_pending_jobs(self):
        """Đọc danh sách các dòng từ Google Sheets có trạng thái chưa hoàn thành (Dành cho kiểm tra thủ công)"""
        records = self.worksheet.get_all_records()
        jobs = []
        for index, row in enumerate(records):
            clean_row = {str(k).strip().lower(): v for k, v in row.items()}
            row_num = index + 2
            status = str(clean_row.get("status", "")).lower()
            
            if status not in ["xong", "completed", "done", "xong ✅"] and "đang chạy" not in status:
                if str(clean_row.get("id_anh_goc", "")).strip():
                    jobs.append({"row_num": row_num})
        return jobs

    def checkout_next_job(self, acc_name):
        """(Queue Worker Mode) Tìm một việc chưa ai làm, ĐÁNH DẤU CHỦ QUYỀN ngay lập tức và trả về job đó."""
        records = self.worksheet.get_all_records()
        for index, row in enumerate(records):
            clean_row = {str(k).strip().lower(): v for k, v in row.items()}
            status = str(clean_row.get("status", "")).lower().strip()
            
            # Chỉ bốc những dòng Chưa chạy HOẶC đã bị Lỗi nhưng đang chờ "Thử lại"
            if status == "" or status == "chờ xử lý" or status == "pending" or "thử lại" in status:
                id_anh_goc = str(clean_row.get("id_anh_goc", "")).strip()
                if not id_anh_goc:
                    continue
                    
                row_num = index + 2
                drive_id = self.extract_drive_id(id_anh_goc)
                
                # CHỐNG ĐỤNG ĐỘ TỪ CÁC TÀI KHOẢN KHÁC: GÂY NHIỄU BẰNG CÁCH KHOÁ ROW TỚI ĐÂY MỚI LÀM
                # (Nhưng đối với folder thì ta phải kiểm tra trước khi lock dưới tư cách Bot)
                try:
                    meta = self.drive_service.files().get(fileId=drive_id, fields="mimeType").execute()
                    if meta.get('mimeType') == 'application/vnd.google-apps.folder':
                        self.update_job_status(row_num, f"Đang bung Folder... 📂 ({acc_name})")
                        print(f"📂 HỐ ĐEN XUẤT HIỆN: Dòng {row_num} là một Thư mục! Đang tiến hành phân rã...")
                        expanded_id = self.expand_folder_into_jobs(row_num, drive_id, row)
                        # Folder đã bung thành dòng mới → skip dòng này, bot sẽ bốc dòng ảnh mới ở vòng sau
                        continue
                except Exception as e:
                    # Nếu báo lỗi permission hoặc lỗi cùi bắp, cứ mặc kệ ném vào lưới như một cái File thông thường
                    pass
                
                
                # Bóc tách xem dòng này đã thử lại thất bại bao nhiêu lần rồi (Ngăn vòng lặp vô hạn)
                retry_count = 0
                if "thử lại" in status:
                    import re
                    match = re.search(r'thử lại (\d+)', status)
                    if match:
                        retry_count = int(match.group(1))

                row_num = index + 2       
                # KHOÁ DÒNG NGAY LẬP TỨC ĐỂ ACC KHÁC KHÔNG DÀNH ĐƯỢC
                self.update_job_status(row_num, f"Đang chạy ({acc_name}) 🤖")
                
                # Trích xuất data
                sl_str = str(clean_row.get("so_luong", "4")).strip()
                sl = int(sl_str) if sl_str.isdigit() else 4
                
                tong_sl_str = str(clean_row.get("tong_so_luong", "")).strip()
                tong_sl = int(tong_sl_str) if tong_sl_str.isdigit() else sl
                
                dl_str = str(clean_row.get("download", "2K")).strip().upper()
                if not dl_str: dl_str = "2K"
                    
                return {
                    "row_num": row_num,
                    "id_anh_goc": id_anh_goc,
                    "prompt": str(clean_row.get("prompt", "")),
                    "so_luong": sl,
                    "tong_so_luong": tong_sl,
                    "aspect_ratio": str(clean_row.get("aspect_ratio", "1:1")),
                    "download_reso": dl_str,
                    "retry_count": retry_count
                }
        return None # Hết việc!

    def extract_drive_id(self, raw_link):
        """Bóc tách GDrive ID từ một đường link lộn xộn"""
        try:
            file_id = raw_link
            if "id=" in raw_link:
                file_id = raw_link.split("id=")[1]
            elif "/d/" in raw_link:
                file_id = raw_link.split("/d/")[1].split("/")[0]
            elif "/folders/" in raw_link:
                file_id = raw_link.split("/folders/")[1].split("?")[0].split("/")[0]
            return file_id.split("&")[0]
        except Exception: return raw_link

    def expand_folder_into_jobs(self, row_num, folder_id, original_row_dict):
        """Hút sạch hình ảnh trong Folder và bơm xuống đáy Bảng tính. KHÔNG đè link folder gốc."""
        query = f"'{folder_id}' in parents and trashed=false and (mimeType contains 'image/jpeg' or mimeType contains 'image/png' or mimeType contains 'image/webp')"
        try:
            results = self.drive_service.files().list(q=query, fields="files(id, name)", pageSize=1000).execute()
            files = results.get('files', [])
            if not files: return None

            headers = self.worksheet.row_values(1)
            first_file_id = files[0]['id']

            # Append TẤT CẢ ảnh (kể cả ảnh đầu) thành dòng mới — KHÔNG đè dòng folder gốc
            new_rows_data = []
            for f in files:
                row_arr = []
                for h in headers:
                    val = original_row_dict.get(h, "")
                    h_lower = str(h).strip().lower()
                    if h_lower == "id_anh_goc": val = f['id']
                    elif h_lower == "status": val = "Chờ xử lý"
                    elif h_lower == "result": val = ""
                    row_arr.append(val)
                new_rows_data.append(row_arr)

            if new_rows_data:
                self.worksheet.append_rows(new_rows_data, value_input_option='USER_ENTERED')
                print(f"🎉 Đã bơm {len(new_rows_data)} tấm ảnh từ Folder xuống đáy Google Sheet!")

            # Đánh dấu dòng folder gốc là đã xử lý — giữ nguyên link folder
            self.update_job_status(row_num, f"Đã bung {len(files)} ảnh ✅")

            return None  # Trả None để bot bỏ qua dòng folder, chạy các dòng ảnh mới
        except Exception as e:
            print(f"⚠️ Lỗi phân rã Thư mục Drive: {e}")
            return None

    def reset_failed_jobs(self):
        """Quét toàn bộ sheet, dòng nào có status chứa 'lỗi' hoặc '❌' thì reset về 'Chờ xử lý' để bot chạy lại"""
        records = self.worksheet.get_all_records()
        headers = self.worksheet.row_values(1)
        status_col_idx = headers.index("status") + 1 if "status" in headers else 5
        count = 0
        for index, row in enumerate(records):
            clean_row = {str(k).strip().lower(): v for k, v in row.items()}
            status = str(clean_row.get("status", "")).strip()
            status_lower = status.lower()
            if "lỗi" in status_lower or "❌" in status or "error" in status_lower:
                row_num = index + 2
                self.worksheet.update_cell(row_num, status_col_idx, "Chờ xử lý")
                print(f"   🔄 Dòng {row_num}: [{status}] → [Chờ xử lý]")
                count += 1
        print(f"✅ Đã reset {count} dòng lỗi về 'Chờ xử lý'.")
        return count

    def update_job_status(self, row_num, status, result_link=""):
        """Cập nhật trạng thái và link kết quả lên Google Sheets. Tự động rà tìm cột theo Header"""
        try:
            # Lấy list Header xem Cột Status và Result nằm ở thứ tự cột số mấy (đề phòng User đảo cột)
            headers = self.worksheet.row_values(1)
            status_col_idx = headers.index("status") + 1 if "status" in headers else 5
            result_col_idx = headers.index("result") + 1 if "result" in headers else 6
            
            self.worksheet.update_cell(row_num, status_col_idx, status)
            if result_link:
                self.worksheet.update_cell(row_num, result_col_idx, result_link)
        except Exception as e:
            print(f"Lỗi update sheet tại dòng {row_num}: {e}")

    def download_file_from_drive(self, file_id, save_path):
        """Tải file ảnh gốc từ Google Drive xuống vùng nhớ tạm của máy tính"""
        try:
            file_id = self.extract_drive_id(file_id)
                
            request = self.drive_service.files().get_media(fileId=file_id)
            fh = io.FileIO(save_path, 'wb')
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            print(f"✅ Đã tải thành công ảnh gốc xuống máy (ID: {...})")
            return True
        except Exception as e:
            print(f"❌ Lỗi Tải File từ G-Drive (Hãy check quyền chia sẻ cho Bot của ID: {file_id}): {e}")
            return False

    def create_drive_folder(self, folder_name, parent_id=None):
        """Tạo một thư mục mới trên Google Drive để chứa kết quả đầu ra"""
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]
            
        file = self.drive_service.files().create(body=file_metadata, fields='id, webViewLink').execute()
        return file.get('id'), file.get('webViewLink')

    def resolve_output_folder(self, file_id, base_output_id):
        """Dynamic Routing: Finds the parent of the input file and routes the output to a {parent_name}_Out subfolder."""
        try:
            # 1. Inspect origin
            safe_id = self.extract_drive_id(file_id)
            file_meta = self.drive_service.files().get(fileId=safe_id, fields="parents").execute()
            parents = file_meta.get('parents', [])
            
            if not parents:
                return base_output_id
                
            # 2. Extract parent folder name
            parent_id = parents[0]
            parent_meta = self.drive_service.files().get(fileId=parent_id, fields="name").execute()
            parent_name = parent_meta.get('name')
            
            if not parent_name or parent_name.lower().strip() == "my drive":
                return base_output_id # Root
                
            out_folder_name = f"{parent_name}_Out"
            
            # 3. Check if out_folder already exists in the Base Output Bucket
            # Escape single quotes in folder name for the query
            safe_query_name = out_folder_name.replace("'", "\\'")
            query = f"'{base_output_id}' in parents and name='{safe_query_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            results = self.drive_service.files().list(q=query, fields="files(id, name)").execute()
            files = results.get('files', [])
            
            if len(files) > 0:
                return files[0]['id'] # Exists, reuse
            else:
                # 4. Doesn't exist, Create on-the-fly
                new_folder_id, _ = self.create_drive_folder(out_folder_name, parent_id=base_output_id)
                return new_folder_id
        except Exception as e:
            print(f"⚠️ Trục trặc định tuyến rễ Thư mục (Dùng lại Thư mục tổng): {e}")
            return base_output_id

    def upload_file_to_drive(self, local_path, file_name, parent_folder_id):
        """Tải một file ảnh từ thư mục tạm /outputs_temp lên thư mục kết quả của GDrive"""
        file_metadata = {
            'name': file_name,
            'parents': [parent_folder_id]
        }
        # Tự động nhận diện MediaType nếu là PNG/JPG
        mimetype = 'image/png' if local_path.endswith('.png') else 'image/jpeg'
        media = MediaFileUpload(local_path, mimetype=mimetype, resumable=True)
        file = self.drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return file.get('id')
