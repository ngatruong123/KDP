# Kế hoạch chi tiết: Bot Auto Tải Ảnh Google Labs (ImageFX)

## Mục tiêu
Tạo một kịch bản (script) tinh gọn và tập trung hoàn toàn vào việc tự động hóa UI của Google Labs. Vòng đời của Script: Truy cập -> Cấu hình độ phân giải -> Nhập Prompt -> Tạo ảnh -> Vét/tải sạch mọi ảnh hiển thị -> Lặp lại luồng cho tới khi thu thập đủ số lượng mục tiêu (KPI).

---

## Cổng Socratic (Socratic Gate)
Để code có thể trỏ chuột trúng và khớp với hệ thống của bạn, cần làm rõ 2 thông tin:
1. **Tinh chỉnh giao diện:** Trên trình duyệt của bạn hiện tại, để chọn "độ phân giải/tỉ lệ ảnh", phần này hiển thị dạng dropdown (thả xuống), hay dạng các button (vuông/chữ nhật) bấm trực tiếp? 
2. **Luồng dữ liệu (Input data):** Việc "tạo đến khi đủ số lượng" là sinh ra từ 1 câu lệnh duy nhất (ví dụ gõ 1 câu lệnh nhưng ép máy chạy click Generate 100 lần để ra 400 góc độ khác nhau), hay lập trình mút từ 1 danh sách 100 câu lệnh khác biệt?

---

## Phân rã tiến trình triển khai (Task Breakdown)

### Phase 1: Chuẩn bị Môi trường và Thiết lập Profile
- [ ] Khởi tạo thư mục `downloads/` để gom ảnh thẳng vào ổ cứng.
- [ ] Setup `playwright` (Python v3).
- [ ] Cấu hình User Data Dir ngầm: Lắp Playwright chạy đính trên Profile Chrome đã login sẵn tài khoản Google của bạn (Bypass Authentication hoàn toàn).
- [ ] Khai báo logic biến khởi đầu: `target_quantity` (ví dụ: 500 ảnh) và `current_downloaded = 0`.

### Phase 2: Phân tích & Viết bộ máy dò tìm Giao diện (Locators)
- [ ] Navigate tới đích `url_google_labs`.
- [ ] Truy vết và viết DOM Locator cho ô TextArea gõ Prompt (tìm theo `placeholder=""` hoặc `role="textbox"`).
- [ ] Truy vết các thông số Format ảnh (Resolution / Aspect Ratio). Bot phải click giả lập chọn đúng tỷ lệ theo tham số cấu hình.
- [ ] Truy vết Nút Generate / Tạo kết quả.

### Phase 3: Vòng lặp Khai thác Cốt lõi (Core Scraping Loop)
- [ ] Dựng `while current_downloaded < target_quantity:`
- [ ] Nhập câu lệnh: Gõ text kết hợp thuật toán Human-delay (gõ trễ ngẫu nhiên từ `50ms` - `120ms` từng ký tự).
- [ ] Kích Hoạt Generate: Click Tạo -> Quá trình treo máy chờ bắt đầu.
- [ ] Bắt tín hiệu hiển thị ảnh: Dùng `wait_for_selector` chờ cho đến khi ảnh kết quả (thường sinh ra 4 ảnh 1 mẻ) hiện lên giao diện.
- [ ] **Mạng lưới Tải file (Download Interception):** 
      - Tìm toàn bộ nút "Tải xuống" được gen ra ở mẻ ảnh này. 
      - Dùng hàm `page.expect_download()` cho mỗi click để xác thực file đã lưu xuống thư mục vật lý.
- [ ] Cập nhật biến đếm số lượng `current_downloaded += số ảnh vừa tải`. So khớp nếu vượt giới hạn thì `break`.

### Phase 4: Ổn định hóa & Vượt chướng ngại (Anti-block)
- [ ] Trạng thái Quota: Nếu website hiện Pop-up "Hết lượt tạo / Limit Exceeded", Tool sẽ đi vào trạng thái ngủ đông `sleep(X phút)` và retry, hoặc báo còi tự tắt.
- [ ] Inject `playwright-stealth`: Ẩn thông số `navigator.webdriver` để tránh Google block IP hoặc captcha bất ngờ.

---

## Phân chia tác vụ (Agent Assignments)
- **Scraper Agent:** Phụ trách toàn bộ thao tác click, gõ phím và điều hướng DOM Element.
- **System Agent:** Đo lường biến đếm, lưu file tĩnh, catch lỗi time-out vòng lặp.

## Tiêu chí Nghiệm thu (Verification Checklist)
- [ ] Run bot bằng console, không yêu cầu xác thực rườm rà.
- [ ] Bot chủ động thao tác click đổi Option/Độ phân giải theo ý muốn trước khi gen.
- [ ] Cứ tạo ra hình nào là vét file lưu cứng về máy hình đó, không bị tuột/click hụt.
- [ ] Trình báo đầy đủ tiến độ ở Terminal console (e.g. `Đã tải 125/500 ảnh...`).
- [ ] Vòng lặp tự động đóng Chrome sạch sẽ khi vừa khít thỏa mãn KPI số lượng.
