import os
import io
import time
import random
import threading
import gspread
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from googleapiclient.errors import HttpError
from dotenv import load_dotenv
from google.auth.transport.requests import AuthorizedSession

# === CROSS-PROCESS BANDWIDTH LIMITER ===
# Giới hạn tối đa 2 bot download/upload Drive cùng lúc (tránh nghẽn mạng)
_LOCK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".drive_locks")
_MAX_CONCURRENT = 2

def _is_pid_alive(pid):
    """Kiểm tra process còn sống không — hoạt động trên cả Windows và Linux."""
    try:
        if os.name == 'nt':
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False

# Dọn lock cũ 1 lần duy nhất khi module được import
def _cleanup_all_stale_locks():
    os.makedirs(_LOCK_DIR, exist_ok=True)
    for i in range(_MAX_CONCURRENT):
        lock_file = os.path.join(_LOCK_DIR, f"slot_{i}.lock")
        if os.path.exists(lock_file):
            try:
                with open(lock_file, 'r') as f:
                    pid = int(f.read().strip())
                if not _is_pid_alive(pid):
                    os.remove(lock_file)
            except Exception:
                try: os.remove(lock_file)
                except Exception: pass

_cleanup_all_stale_locks()

class _DriveBandwidthLimiter:
    """File-based semaphore để giới hạn số process gọi Drive API đồng thời."""

    def __init__(self):
        self._slot = None

    def acquire(self, timeout=30):
        """Chờ đến khi có slot trống (tối đa 30s)."""
        start = time.time()
        attempts = 0
        while time.time() - start < timeout:
            for i in range(_MAX_CONCURRENT):
                lock_file = os.path.join(_LOCK_DIR, f"slot_{i}.lock")
                try:
                    fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(fd, str(os.getpid()).encode())
                    os.close(fd)
                    self._slot = lock_file
                    return True
                except FileExistsError:
                    continue
            # Mỗi 5 vòng chờ, dọn lock chết 1 lần (không spam tasklist)
            attempts += 1
            if attempts % 5 == 0:
                for i in range(_MAX_CONCURRENT):
                    lock_file = os.path.join(_LOCK_DIR, f"slot_{i}.lock")
                    if os.path.exists(lock_file):
                        try:
                            with open(lock_file, 'r') as f:
                                pid = int(f.read().strip())
                            if not _is_pid_alive(pid):
                                os.remove(lock_file)
                        except Exception:
                            try: os.remove(lock_file)
                            except Exception: pass
            time.sleep(random.uniform(0.5, 1.5))
        # Timeout → cho chạy luôn
        return False

    def release(self):
        """Trả lại slot."""
        if self._slot and os.path.exists(self._slot):
            try: os.remove(self._slot)
            except Exception: pass
            self._slot = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()

_bandwidth_limiter_lock = threading.Lock()

load_dotenv()

# Cấu hình chứng thực
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
TOKEN_FILE = 'config/token.json'
CLIENT_SECRET_FILE = 'config/client_secret.json'

def _retry_api(func, max_retries=3, label="API"):
    """Retry Google API call với exponential backoff + jitter"""
    for attempt in range(max_retries):
        try:
            return func()
        except HttpError as e:
            status_code = e.resp.status if e.resp else 0
            # 403 rate limit, 429 quota, 500/503 server error → retry
            if status_code in (403, 429, 500, 503) and attempt < max_retries - 1:
                wait = (2 ** attempt) + random.uniform(0, 1)
                print(f"⏳ {label}: lỗi {status_code}, thử lại sau {wait:.1f}s... (lần {attempt + 1}/{max_retries})")
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            if attempt < max_retries - 1:
                wait = (2 ** attempt) + random.uniform(0, 1)
                print(f"⏳ {label}: lỗi [{e}], thử lại sau {wait:.1f}s... (lần {attempt + 1}/{max_retries})")
                time.sleep(wait)
            else:
                raise

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
        print("  [1/4] Đang xác thực Google Sheets...")
        self.gs_client = gspread.authorize(self.creds)
        self.sheet_id = os.getenv("SPREADSHEET_ID")
        if not self.sheet_id:
            raise ValueError("Thiếu biến SPREADSHEET_ID ở file .env")

        print(f"  [2/4] Đang mở Sheet (ID: {self.sheet_id[:15]}...)...")
        self.worksheet = self.gs_client.open_by_key(self.sheet_id).sheet1
        print("  ✅ Kết nối Google Sheets thành công!")

        # Google Drive Client
        print("  [3/4] Đang kết nối Google Drive...")
        self.drive_service = build('drive', 'v3', credentials=self.creds)
        print("  ✅ Kết nối Google Drive thành công!")

        # Cache header để không gọi API mỗi lần update
        print("  [4/4] Đang đọc Header...")
        self._headers = self.worksheet.row_values(1)
        self._status_col = self._headers.index("status") + 1 if "status" in self._headers else 5
        self._result_col = self._headers.index("result") + 1 if "result" in self._headers else 6
        print(f"  ✅ Header: {self._headers}")

    def _ensure_creds(self):
        """Tự động refresh token nếu hết hạn — tránh lỗi khi bot chạy lâu"""
        if self.creds and self.creds.expired and self.creds.refresh_token:
            try:
                self.creds.refresh(Request())
                self.drive_service = build('drive', 'v3', credentials=self.creds)
                print("🔑 Token đã được tự động refresh.")
            except Exception as e:
                print(f"⚠️ Không refresh được token: {e}")

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

    def checkout_next_job(self, acc_name, reset_errors=False):
        """(Queue Worker Mode) Tìm một việc chưa ai làm, ĐÁNH DẤU CHỦ QUYỀN ngay lập tức và trả về job đó."""
        records = self.worksheet.get_all_records()

        # Reset lỗi trong cùng 1 lần tải sheet (không cần gọi get_all_records thêm lần nữa)
        if reset_errors:
            error_cells = []
            for index, row in enumerate(records):
                clean_row = {str(k).strip().lower(): v for k, v in row.items()}
                status = str(clean_row.get("status", "")).strip()
                status_lower = status.lower()
                if "lỗi" in status_lower or "❌" in status or "error" in status_lower:
                    error_cells.append(gspread.Cell(index + 2, self._status_col, "Chờ xử lý"))
            if error_cells:
                self.worksheet.update_cells(error_cells, value_input_option='USER_ENTERED')
                print(f"🔄 Đã reset {len(error_cells)} dòng lỗi về 'Chờ xử lý'.")
                # Reload records vì status đã thay đổi
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

                # Bóc tách retry count trước khi lock
                retry_count = 0
                if "thử lại" in status:
                    import re
                    match = re.search(r'thử lại (\d+)', status)
                    if match:
                        retry_count = int(match.group(1))

                # KHOÁ DÒNG NGAY LẬP TỨC ĐỂ ACC KHÁC KHÔNG DÀNH ĐƯỢC (lock TRƯỚC khi check folder)
                self.update_job_status(row_num, f"Đang chạy ({acc_name}) 🤖")

                # Kiểm tra nếu là folder thì bung ra thành các dòng ảnh
                try:
                    meta = self.drive_service.files().get(fileId=drive_id, fields="mimeType").execute()
                    if meta.get('mimeType') == 'application/vnd.google-apps.folder':
                        self.update_job_status(row_num, f"Đang bung Folder... 📂 ({acc_name})")
                        print(f"📂 HỐ ĐEN XUẤT HIỆN: Dòng {row_num} là một Thư mục! Đang tiến hành phân rã...")
                        self.expand_folder_into_jobs(row_num, drive_id, row)
                        continue
                except Exception as e:
                    pass
                
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
        # Đánh dấu TRƯỚC khi bung — chống duplicate khi nhiều bot chạy song song
        self.update_job_status(row_num, "Đang bung Folder... 📂")

        query = f"'{folder_id}' in parents and trashed=false and (mimeType contains 'image/jpeg' or mimeType contains 'image/png' or mimeType contains 'image/webp')"
        try:
            results = self.drive_service.files().list(q=query, fields="files(id, name)", pageSize=1000).execute()
            files = results.get('files', [])
            if not files:
                self.update_job_status(row_num, "Folder rỗng ⚠️")
                return None

            headers = self._headers

            # Append TẤT CẢ ảnh thành dòng mới — KHÔNG đè dòng folder gốc
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

            self.update_job_status(row_num, f"Đã bung {len(files)} ảnh ✅")

            return None  # Trả None để bot bỏ qua dòng folder, chạy các dòng ảnh mới
        except Exception as e:
            print(f"⚠️ Lỗi phân rã Thư mục Drive: {e}")
            return None

    def reset_failed_jobs(self):
        """Quét toàn bộ sheet, dòng nào có status chứa 'lỗi' hoặc '❌' thì reset về 'Chờ xử lý' (batch 1 lần)"""
        records = self.worksheet.get_all_records()
        headers = self.worksheet.row_values(1)
        status_col_idx = headers.index("status") + 1 if "status" in headers else 5

        # Thu thập tất cả dòng cần reset
        cells_to_update = []
        for index, row in enumerate(records):
            clean_row = {str(k).strip().lower(): v for k, v in row.items()}
            status = str(clean_row.get("status", "")).strip()
            status_lower = status.lower()
            if "lỗi" in status_lower or "❌" in status or "error" in status_lower:
                row_num = index + 2
                cells_to_update.append(gspread.Cell(row_num, status_col_idx, "Chờ xử lý"))
                print(f"   🔄 Dòng {row_num}: [{status}] → [Chờ xử lý]")

        # Batch update 1 lần duy nhất thay vì N lần API call
        if cells_to_update:
            self.worksheet.update_cells(cells_to_update, value_input_option='USER_ENTERED')
        print(f"✅ Đã reset {len(cells_to_update)} dòng lỗi về 'Chờ xử lý'.")
        return len(cells_to_update)

    def _log_error(self, context, error):
        """Ghi lỗi vào error_log.txt"""
        try:
            with open("error_log.txt", "a", encoding="utf-8") as f:
                f.write(f"\n{'='*50}\n[GDrive] {context}: {error}\n")
        except Exception:
            pass

    def update_job_status(self, row_num, status, result_link=""):
        """Cập nhật trạng thái và link kết quả lên Google Sheets."""
        try:
            def _do_update():
                self.worksheet.update_cell(row_num, self._status_col, status)
                if result_link:
                    self.worksheet.update_cell(row_num, self._result_col, result_link)

            _retry_api(_do_update, max_retries=3, label=f"UpdateSheet row {row_num}")
        except Exception as e:
            print(f"Lỗi update sheet tại dòng {row_num}: {e}")
            self._log_error(f"UpdateSheet row {row_num}", e)

    def download_file_from_drive(self, file_id, save_path):
        """Tải file ảnh gốc từ Google Drive xuống vùng nhớ tạm của máy tính"""
        self._ensure_creds()
        limiter = _DriveBandwidthLimiter()
        try:
            file_id = self.extract_drive_id(file_id)

            def _do_download():
                # Download vào RAM trước, rồi ghi ra file — tránh OS file lock lỗi
                request = self.drive_service.files().get_media(fileId=file_id)
                buffer = io.BytesIO()
                downloader = MediaIoBaseDownload(buffer, request)
                done = False
                while done is False:
                    status, done = downloader.next_chunk()
                # Ghi từ RAM ra ổ cứng
                with open(save_path, 'wb') as f:
                    f.write(buffer.getvalue())
                buffer.close()

            limiter.acquire()
            _retry_api(_do_download, max_retries=5, label=f"Download {file_id[:8]}")
            limiter.release()
            print(f"✅ Đã tải thành công ảnh gốc xuống máy (ID: {file_id[:8]}...)")
            return True
        except Exception as e:
            limiter.release()
            print(f"❌ Lỗi Tải File từ G-Drive (ID: {file_id}): {e}")
            self._log_error(f"Download {file_id}", e)
            if os.path.exists(save_path):
                try: os.remove(save_path)
                except Exception: pass
            return False

    def create_drive_folder(self, folder_name, parent_id=None):
        """Tạo một thư mục mới trên Google Drive để chứa kết quả đầu ra"""
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]

        def _do_create():
            return self.drive_service.files().create(body=file_metadata, fields='id, webViewLink').execute()

        file = _retry_api(_do_create, max_retries=3, label=f"CreateFolder {folder_name}")
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
        self._ensure_creds()
        limiter = _DriveBandwidthLimiter()
        try:
            file_metadata = {
                'name': file_name,
                'parents': [parent_folder_id]
            }
            mimetype = 'image/png' if local_path.endswith('.png') else 'image/jpeg'

            def _do_upload():
                media = MediaFileUpload(local_path, mimetype=mimetype, resumable=True)
                return self.drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()

            limiter.acquire()
            result = _retry_api(_do_upload, max_retries=4, label=f"Upload {file_name}")
            limiter.release()
            return result.get('id')
        except Exception as e:
            limiter.release()
            print(f"❌ Lỗi Upload {file_name}: {e}")
            self._log_error(f"Upload {file_name}", e)
            return None
