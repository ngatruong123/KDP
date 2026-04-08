from google_api import GoogleManager

gmanager = GoogleManager()

try:
    # Lấy tên của output_folder_id đang dùng trong main.py
    folder_id = "1yKY-v8gU8O0hbnS4XJc5GU1L2U3oMpKR"
    file_info = gmanager.drive_service.files().get(fileId=folder_id, fields="name").execute()
    print("🚀 Tên thư mục Gốc chứa ảnh:", file_info.get('name'))
except Exception as e:
    print("Error:", e)
