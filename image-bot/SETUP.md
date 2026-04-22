# Hướng dẫn cài đặt & chạy Image Bot

## 1. Clone code

```bash
git clone git@github.com:ngatruong123/remakeai.git
cd remakeai/image-bot
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

# (Tùy chọn) Cho fingerprint-chromium
FINGERPRINT_CHROMIUM_PATH=/path/to/fingerprint-chromium
FINGERPRINT_SEED_SECRET=your-secret-key
```

### File `config/`
Copy 2 file từ máy đã cấu hình sẵn:
- `config/client_secret.json` — Google Cloud API credentials
- `config/token.json` — Google OAuth token (tự tạo khi chạy lần đầu nếu chưa có)

---

## 5. Cài fingerprint-chromium (tùy chọn)

Nếu muốn dùng fingerprint-chromium thay vì Chrome mặc định (chống detect tốt hơn):

1. Tải fingerprint-chromium binary phù hợp OS
2. Set env var trong `.env`:
   ```
   FINGERPRINT_CHROMIUM_PATH=/absolute/path/to/fingerprint-chromium
   FINGERPRINT_SEED_SECRET=my-secret-123
   ```
3. Thêm flag `--fingerprint` khi chạy bot

Mỗi account sẽ có fingerprint seed riêng (deterministic từ tên acc + secret).

---

## 6. Tạo profile bot (đăng nhập Google)

Chạy từng acc **không headless** để mở Chrome đăng nhập:

```bash
source venv/bin/activate

# Chrome mặc định
python3 main.py --acc acc1

# Với fingerprint-chromium
python3 main.py --acc acc1 --fingerprint

# Có proxy
python3 main.py --acc acc1 --proxy http://user:pass@ip:port
```

Bot mở Chrome -> đăng nhập Google -> vào được Canvas -> tắt bot (Ctrl+C).
Profile lưu tại `chrome_profile_acc1/`.

Lặp lại cho mỗi acc: `acc2`, `acc3`...

---

## 7. Google Sheets format

### Cột bắt buộc
| Cột | Mô tả |
|-----|--------|
| `id_anh_goc` | Google Drive file ID hoặc link (hỗ trợ `ID1\|ID2` cho multi-image) |
| `prompt` | Prompt tạo ảnh |
| `status` | Trạng thái job (bot tự cập nhật) |
| `result` | Link kết quả (bot tự cập nhật) |

### Cột tùy chọn
| Cột | Mô tả | Mặc định |
|-----|--------|----------|
| `so_luong` | Số ảnh mỗi lần generate | `4` |
| `tong_so_luong` | Tổng số ảnh cần tạo | = `so_luong` |
| `aspect_ratio` | Tỉ lệ ảnh (`1:1`, `3:4`, `16:9`...) | `1:1` |
| `download` | Độ phân giải tải (`2K`, `4K`) | `2K` |
| `reference_image` | Google Drive ID của ảnh tham chiếu (paste vào editor trước khi generate) | _(trống)_ |

---

## 8. Chạy bot

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

# Dùng fingerprint-chromium
python3 main.py --acc acc1 --fingerprint

# Dùng prompt template
python3 main.py --acc acc1 --template "{prompt}, watercolor style, high quality"

# Kết hợp tất cả
python3 main.py --acc acc1 --headless --fingerprint --proxy http://user:pass@ip:port --template "{prompt}, best quality"
```

### Chạy farm (nhiều bot)
```bash
source venv/bin/activate

# Cơ bản: 3 bot chạy song song
python3 start_farm.py --accounts acc1,acc2,acc3

# Có backup: khi bot lỗi 3 lần liên tiếp -> tự thay bằng bot dự bị
python3 start_farm.py --accounts acc1,acc2,acc3 --backup-accounts backup1,backup2

# Headless + proxy
python3 start_farm.py --accounts acc1,acc2,acc3 --headless --proxy http://user:pass@ip:port

# Với fingerprint-chromium (mỗi bot tự nhận CDP port 9222, 9223, 9224...)
python3 start_farm.py --accounts acc1,acc2,acc3 --fingerprint

# Full options
python3 start_farm.py --accounts acc1,acc2,acc3 --backup-accounts backup1,backup2 --headless --no-cut --proxy http://user:pass@ip:port --fingerprint
```

---

## 9. Các flag có sẵn

### `main.py`
| Flag | Mô tả |
|------|--------|
| `--acc <tên>` | Tên tài khoản (mặc định: `default`) |
| `--headless` | Chạy ẩn, không mở Chrome |
| `--no-cut` | Chỉ upscale, không cắt nền |
| `--resume-from <acc_cũ>` | Nhặt lại dòng kẹt của acc cũ bị crash |
| `--proxy <url>` | Proxy server (vd: `http://user:pass@ip:port`) |
| `--fingerprint` | Dùng fingerprint-chromium thay vì Chrome mặc định |
| `--cdp-port <port>` | CDP port cho fingerprint browser (mặc định: `9222`) |
| `--template <str>` | Prompt template (mặc định: `{prompt}`) |

### `start_farm.py`
| Flag | Mô tả |
|------|--------|
| `--accounts <a,b,c>` | Danh sách acc chính (bắt buộc) |
| `--backup-accounts <x,y>` | Danh sách acc dự bị |
| `--headless` | Tất cả bot chạy ẩn |
| `--no-cut` | Tất cả bot chỉ upscale |
| `--proxy <url>` | Tất cả bot chạy qua proxy |
| `--fingerprint` | Tất cả bot dùng fingerprint-chromium (CDP port tự tăng: 9222, 9223...) |

---

## 10. Cấu trúc file

```
image-bot/
├── main.py                 # Entry point - single bot worker
├── bot.py                  # Browser automation core (Playwright)
├── start_farm.py           # Multi-bot farm orchestrator
├── google_api.py           # Google Sheets & Drive integration
├── kdp_local_bot.py        # Image post-processing (upscale + remove bg)
├── browser_launcher.py     # Fingerprint-chromium launcher (CDP)
├── events.py               # JSON structured event logging
├── state_manager.py        # Local atomic state for job resume
├── prompt_builder.py       # Prompt template system
├── check_profiles.py       # Utility: open Chrome profiles for manual check
├── test_api.py             # Utility: test Google API connectivity
├── config/
│   ├── client_secret.json  # Google Cloud credentials
│   └── token.json          # OAuth token (auto-generated)
├── bin/                    # Real-ESRGAN binary
├── .env                    # Environment variables
├── chrome_profile_<acc>/   # Browser profiles (per account)
├── outputs_temp_<acc>/     # Temp download dir (per account)
├── state_<acc>.json        # Local state file (per account, auto-generated)
└── logs/                   # Farm log files (per account)
```

---

## 11. State manager & Resume

Bot tự tạo file `state_<acc>.json` để track tiến trình từng job. Khi bot bị kill giữa chừng:

1. Restart bot bình thường
2. Bot tự detect job bị interrupted, đánh dấu failed
3. Google Sheets queue sẽ re-process các dòng đó

State file chỉ là backup local, Google Sheets vẫn là source of truth.

---

## 12. JSON Events

Khi chạy, bot emit JSON events ra stdout (hữu ích cho monitoring/pipeline):

```json
{"event": "bot_started", "ts": 1719000000.0, "acc": "acc1", "fingerprint": false}
{"event": "job_started", "ts": 1719000001.0, "row": 5, "prompt": "a cute cat..."}
{"event": "generation_done", "ts": 1719000060.0, "row": 5, "count": 4}
{"event": "job_done", "ts": 1719000120.0, "row": 5, "uploaded": 8}
{"event": "job_failed", "ts": 1719000120.0, "row": 5, "reason": "no_output"}
{"event": "bot_stopped", "ts": 1719000200.0, "acc": "acc1"}
```

---

## 13. Xem log

```bash
# Xem log realtime của 1 bot
tail -f logs/acc1.log

# Xem tất cả log
ls logs/

# Filter JSON events từ log
grep '"event"' logs/acc1.log | jq .
```

---

## 14. Format proxy

Nếu proxy có dạng `ip:port:user:pass`, chuyển thành:
```
http://user:pass@ip:port
```

Ví dụ: `180.93.229.161:51538:objhpjeu:hBLU6f6p` -> `http://objhpjeu:hBLU6f6p@180.93.229.161:51538`
