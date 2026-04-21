# Hướng dẫn cài đặt & chạy Image Bot

## 1. Clone code

```bash
git clone git@github.com:ngatruong123/KDP.git
cd KDP/image-bot
```

---

## 2. Cài đặt môi trường

### macOS
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install "rembg[cpu]"
playwright install chromium
```

### Ubuntu
```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip libvulkan1 -y
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install "rembg[cpu]" onnxruntime
playwright install chromium
playwright install-deps
```

### Windows
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
pip install "rembg[cpu]"
playwright install chromium
```

---

## 3. Cài Upscale Engine (realesrgan)

### macOS
```bash
mkdir -p bin && cd bin
curl -LO https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-macos.zip
unzip realesrgan-ncnn-vulkan-20220424-macos.zip
chmod +x realesrgan-ncnn-vulkan
rm realesrgan-ncnn-vulkan-20220424-macos.zip
cd ..
```

### Ubuntu
```bash
mkdir -p bin && cd bin
wget https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-ubuntu.zip
unzip realesrgan-ncnn-vulkan-20220424-ubuntu.zip
chmod +x realesrgan-ncnn-vulkan
rm realesrgan-ncnn-vulkan-20220424-ubuntu.zip
cd ..
```

### Windows
```bash
mkdir bin && cd bin
curl -LO https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip
tar -xf realesrgan-ncnn-vulkan-20220424-windows.zip
del realesrgan-ncnn-vulkan-20220424-windows.zip
cd ..
```

---

## 4. Cấu hình

### File `.env`
Tạo file `.env` tại thư mục `image-bot/`:
```
SPREADSHEET_ID=your_google_sheet_id_here
```

### File `config/`
Copy 2 file từ máy đã cấu hình sẵn:
- `config/client_secret.json` — Google Cloud API credentials
- `config/token.json` — Google OAuth token (tự tạo khi chạy lần đầu nếu chưa có)

---

## 5. Tạo profile bot (đăng nhập Google)

Chạy từng acc **không headless** để mở Chrome đăng nhập:

```bash
source venv/bin/activate

# Không proxy
python3 main.py --acc acc1

# Có proxy
python3 main.py --acc acc1 --proxy http://user:pass@ip:port
```

Bot mở Chrome → đăng nhập Google → vào được Canvas → tắt bot (Ctrl+C).
Profile lưu tại `chrome_profile_acc1/`.

Lặp lại cho mỗi acc: `acc2`, `acc3`...

---

## 6. Chạy bot

### Chạy 1 bot đơn lẻ
```bash
source venv/bin/activate

# Cơ bản
python3 main.py --acc acc1

# Headless (không mở Chrome)
python3 main.py --acc acc1 --headless

# Chỉ upscale, không cắt nền
python3 main.py --acc acc1 --no-cut

# Có proxy
python3 main.py --acc acc1 --proxy http://user:pass@ip:port

# Kết hợp
python3 main.py --acc acc1 --headless --proxy http://user:pass@ip:port
```

### Chạy farm (nhiều bot)
```bash
source venv/bin/activate

# Cơ bản: 3 bot chạy song song
python3 start_farm.py --accounts acc1,acc2,acc3

# Có backup: khi bot lỗi 3 lần liên tiếp → tự thay bằng bot dự bị
python3 start_farm.py --accounts acc1,acc2,acc3 --backup-accounts backup1,backup2

# Headless + proxy
python3 start_farm.py --accounts acc1,acc2,acc3 --headless --proxy http://user:pass@ip:port

# Full options
python3 start_farm.py --accounts acc1,acc2,acc3 --backup-accounts backup1,backup2 --headless --no-cut --proxy http://user:pass@ip:port
```

---

## 7. Các flag có sẵn

### `main.py`
| Flag | Mô tả |
|------|--------|
| `--acc <tên>` | Tên tài khoản (mặc định: `default`) |
| `--headless` | Chạy ẩn, không mở Chrome |
| `--no-cut` | Chỉ upscale, không cắt nền |
| `--resume-from <acc_cũ>` | Nhặt lại dòng kẹt của acc cũ bị crash |
| `--proxy <url>` | Proxy server (vd: `http://user:pass@ip:port`) |

### `start_farm.py`
| Flag | Mô tả |
|------|--------|
| `--accounts <a,b,c>` | Danh sách acc chính (bắt buộc) |
| `--backup-accounts <x,y>` | Danh sách acc dự bị |
| `--headless` | Tất cả bot chạy ẩn |
| `--no-cut` | Tất cả bot chỉ upscale |
| `--proxy <url>` | Tất cả bot chạy qua proxy |

---

## 8. Xem log

```bash
# Xem log realtime của 1 bot
tail -f logs/acc1.log

# Xem tất cả log
ls logs/
```

---

## 9. Format proxy

Nếu proxy có dạng `ip:port:user:pass`, chuyển thành:
```
http://user:pass@ip:port
```

Ví dụ: `180.93.229.161:51538:objhpjeu:hBLU6f6p` → `http://objhpjeu:hBLU6f6p@180.93.229.161:51538`
