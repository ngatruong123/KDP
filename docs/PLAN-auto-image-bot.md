# Kiến trúc xây dựng Bot tự động tạo và quản lý ảnh (Playwright Automation)

## Mục tiêu Dự án
Xây dựng một hệ thống bot tự động trích xuất ảnh trên nền tảng Web thông qua Playwright, hỗ trợ khả năng tải xuống an toàn và kiến trúc khởi chạy mở rộng (Modular Architecture) để dễ dàng ráp nối các tính năng hậu kì như: gộp file, nén file và tự động gửi thông báo.

---

## Cổng Socratic (Socratic Gate)
Trước khi bước vào giai đoạn viết code, chúng ta cần xác định và làm rõ ngữ cảnh ở các chiều mở rộng:
1. **Quản lý Nguồn vào (Input):** Danh sách hàng loạt câu lệnh (prompts) của bạn sẽ được thiết lập từ đâu? (Bảng tính Google Sheets, file Excel, hay cấu hình file TXT đơn giản)?
2. **Hình thức gộp file:** Khi mở rộng chức năng "gộp file", bạn hiểu theo nghĩa đóng gói toàn bộ thành 1 file nén `.zip`, nối các ảnh thành 1 file tài liệu `.pdf`, hay gộp nhiều ảnh thành 1 mảng lưới (grid hình ảnh) `.jpg` duy nhất?
3. **Kênh tự động gửi:** Hệ thống của bạn dự kiến sẽ tự động bắn kết quả tới đâu? (Gửi message + file qua Telegram Bot, Email thông báo, hay tự động đồng bộ đẩy lên Google Drive)?

---

## Phân rã tiến trình triển khai (Task Breakdown)

### Phase 1: Nền tảng (Foundation) & Thiết lập Môi trường thiết yếu
- [ ] Khởi tạo Cấu trúc Dự án Python: Tạo môi trường ảo (virtualenv), cài đặt `playwright`, `playwright-stealth`, `python-dotenv`.
- [ ] Quy hoạch Thư mục (Folder Structure):
  - `config/` (Chứa tệp môi trường `.env`, định nghĩa Locators, Settings).
  - `input/` (Chứa danh sách từ khóa csv/txt).
  - `downloads/` (Kho lưu ảnh thô từ bot tải về).
  - `exports/` (Nơi chứa file sau khi đã gộp/chế bản).
- [ ] **Bypass Authentication:** Khởi tạo Chrome/Edge Persistent Context. Trỏ đường dẫn hệ thống Playwright đọc thẳng vào thư mục `User Data` của trình duyệt đang dùng để bảo lưu cookie và auth token.

### Phase 2: Lõi Tự động hóa Web (Core Web Engine)
- [ ] Viết Module Điều hướng: Truy cập Web mục tiêu, kích hoạt kịch bản ẩn danh dạng Stealth Mode, áp dụng kỹ thuật giả lập (Human-typing delay).
- [ ] Xây dựng Loop Logic (Queue system): Cơ chế duyệt qua danh sách Prompts với khoảng thời gian delay ngẫu nhiên an toàn để chống Spam/Ban rate.
- [ ] Tương tác UI (DOM Elements): 
  - Build locator động (không dính code cứng) cho: Search box, Generate button.
- [ ] Quản lý sự kiện tải xuống (Download Handler): Bắt luồng `page.expect_download()` lưu file chính xác dạng đồng bộ hóa vào thư mục `/downloads`. Đảm bảo file ảnh tải xong hoàn toàn bot mới được đi tiếp.
- [ ] Recovery & Exception Handling: Thiết lập logic chụp ảnh màn hình lưu thư mục `logs/` ngay khi Web trả về lỗi (hoặc gặp Captcha) để tạm dừng kịch bản và báo lỗi.

### Phase 3: Module Mở rộng 1 - Tiền xử lý & Hậu kỳ (Post-Processing)
*(Được thiết kế độc lập chuẩn OOP, sẽ kích hoạt sau khi luồng Phase 2 ngắt/báo kết thúc batch)*
- [ ] Khởi tạo module `processor.py`.
- [ ] **Thực hiện Gộp Ảnh (Collage/PDF):** Dùng thư viện `Pillow` (PIL) cho việc append các ảnh vào với nhau làm catalog, hoặc gộp ra dạng `.pdf` báo cáo.
- [ ] **Thực hiện Đóng gói (Zipping):** Thư viện `shutil` tạo một tệp `{date_batch}.zip` đóng băng toàn bộ thư mục raw output để dễ gửi/nhận.
- [ ] Dọn dẹp (Cleanup Manager): Logic tự động xóa thư mục bộ nhớ đệm (cache ảnh thô) sau khi đã "gói" thành công để giải phóng ổ cứng.

### Phase 4: Module Mở rộng 2 - Phân phối Tự động (Distribution)
*(Plugin kết nối API bên thứ 3)*
- [ ] Tách bạch dữ liệu nhạy cảm (Private keys, API token, Password) cất vào hệ thống biến môi trường bí mật `.env`.
- [ ] **Route A (Gửi Telegram):** Dùng `python-telegram-bot` hoặc RestAPI bắn thư thông báo và upload tệp tin Zip thẳng về tài khoản cá nhân.
- [ ] **Route B (Google Drive Sync):** Viết module thông qua `google-api-python-client` tạo một folder mới trên driver theo ngày và đồng bộ file lên mây.
- [ ] Báo cáo (Notification Hook): Kết thúc toàn chu trình, hệ thống tổng kết log (thất bại bao nhiêu, thành công bao nhiêu) nội dung đi kèm file.

### Phase 5: Hợp nhất (Orchestration) và Tối ưu (Optimization)
- [ ] Refactor mã nguồn: Gom các module lại bằng tính đóng gói Class Component (ví dụ `class Scraper`, `class Archiver`, `class Notifier`) không cho ảnh hưởng chéo nhau.
- [ ] Khởi tạo Controller Center (`main.py`): Dùng `argparse` thiết lập command line arguments. Giúp bạn ra hình thái gọi terminal chuyên nghiệp. VD: `python main.py --run --pack-zip --send-tele`.

---

## Tiêu chí Nghiệm thu (Verification Checklist)
- [ ] Chrome Bot Bypass không bắt xác thực nhập mã bảo vệ lại.
- [ ] Danh sách input từ khóa test 5 lệnh chạy trơn tru lưu ảnh chất lượng gốc.
- [ ] Cơ chế gộp file thực thi mà không có file ảnh rác sau quá trình đóng gói.
- [ ] File đến đích cuối ở Nền tảng phân phối mà không bị bóp méo dữ liệu hoặc timeout.
