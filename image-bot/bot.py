import os
import asyncio
from playwright.async_api import async_playwright

class ImageBotCore:
    def __init__(self, acc_name="default", headless=False):
        self.acc_name = acc_name
        self.headless = headless
        self.user_data_dir = f"chrome_profile_{acc_name}" if acc_name != "default" else "chrome_profile"
        self.download_dir = f"outputs_temp_{acc_name}" if acc_name != "default" else "outputs_temp"
        os.makedirs(self.download_dir, exist_ok=True)

    async def init_browser(self):
        """Khởi tạo trình duyệt Playwright với Profile cá nhân để vượt Đăng nhập"""
        self.playwright = await async_playwright().start()
        # Ép User-Agent giống hệt máy Mac xịn để qua mặt lớp chống Bot chặn thẻ Headless của Google
        fake_user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        
        # Mở Chrome theo thông số headless từ file điều khiển
        self.browser = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=os.path.abspath(self.user_data_dir),
            channel="chrome",
            headless=self.headless,
            user_agent=fake_user_agent,
            accept_downloads=True,
            downloads_path=os.path.abspath(self.download_dir),
            viewport={"width": 1280, "height": 720},
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-features=DownloadBubble,DownloadBubbleV2',
                # === TỐI ƯU RAM/CPU CHO MULTI-BOT ===
                '--disable-gpu',                    # Tắt GPU render (tiết kiệm VRAM + RAM)
                '--disable-dev-shm-usage',          # Tránh crash /dev/shm trên máy ít RAM
                '--disable-extensions',             # Tắt extension ăn RAM
                '--disable-background-networking',  # Tắt request nền
                '--disable-sync',                   # Tắt đồng bộ Chrome
                '--disable-translate',              # Tắt dịch trang
                '--no-first-run',
                '--disable-component-update',       # Tắt tự update component
                '--disable-background-timer-throttling',
                '--js-flags=--max-old-space-size=256',  # Giới hạn JS heap 256MB/tab
            ]
        )
        # Dùng tab có sẵn hoặc tạo mới (persistent context luôn mở sẵn 1 tab)
        if self.browser.pages:
            self.page = self.browser.pages[0]
            # Đóng tab thừa (giữ lại tab đầu tiên)
            for p in self.browser.pages[1:]:
                await p.close()
        else:
            self.page = await self.browser.new_page()

    async def dismiss_popups(self):
        """Tự động tắt mọi popup/dialog/overlay chặn giao diện"""
        dismissed = 0
        try:
            # 1. Dialog/Modal: tìm nút đóng phổ biến
            close_selectors = [
                "button[aria-label='Close']", "button[aria-label='Đóng']",
                "button:has-text('Đóng')", "button:has-text('Close')",
                "button:has-text('Got it')", "button:has-text('Đã hiểu')",
                "button:has-text('OK')", "button:has-text('Dismiss')",
                "button:has-text('Skip')", "button:has-text('Bỏ qua')",
                "button:has-text('No thanks')", "button:has-text('Không, cảm ơn')",
                "button:has-text('Maybe later')", "button:has-text('Để sau')",
                "button:has-text('Accept')", "button:has-text('Chấp nhận')",
                "button:has-text('I agree')", "button:has-text('Đồng ý')",
                "button:has-text('Continue')", "button:has-text('Tiếp tục')",
            ]
            for sel in close_selectors:
                try:
                    btn = self.page.locator(sel).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click(force=True, timeout=3000)
                        dismissed += 1
                        await asyncio.sleep(1)
                except Exception:
                    pass

            # 2. Overlay/backdrop: xóa bằng JS
            await self.page.evaluate('''() => {
                document.querySelectorAll(
                    '[role="dialog"], [role="alertdialog"], .modal, .overlay, .backdrop, ' +
                    '[class*="modal"], [class*="popup"], [class*="overlay"], [class*="dialog"], ' +
                    '[role="alert"], [role="status"], snack-bar-container, .mat-mdc-snack-bar-container, ' +
                    '[class*="consent"], [class*="cookie"], [class*="banner"]'
                ).forEach(el => el.remove());
            }''')

            # 3. Escape để đóng bất kỳ popup nào còn sót
            await self.page.keyboard.press("Escape")
            await asyncio.sleep(0.5)

        except Exception:
            pass

        if dismissed > 0:
            print(f"🧹 Đã tắt {dismissed} popup chặn giao diện")
        return dismissed

    async def check_login_and_navigate(self):
        await self.page.goto("https://labs.google/fx/vi/tools/flow", wait_until="domcontentloaded")
        print("🌍 Đã mở URL Google Labs Flow Mới!")

        await asyncio.sleep(3)
        await self.dismiss_popups()

        print("🔍 Đang rà soát trạng thái...")
        try:
            # Thử chờ 20s xem có sẵn File Input không (Trường hợp Web tải thẳng vào Canvas cũ)
            await self.page.wait_for_selector("input[type='file']", state="attached", timeout=20000)
            print("✅ Đã ở sẵn trong giao diện Canvas!")
        except Exception:
            # Khả năng cao đang ở Dashboard (Trang chủ Flow) hoặc Chưa Đăng Nhập
            try:
                # Chờ tối đa 20s cho UI nút Tạo web hiện ra (4 Chrome ẩn khiến CPU lag gắt, React load cực chậm)
                new_btn_selector = "button:has-text('Dự án mới'), div[role='button']:has-text('Dự án mới'), span:has-text('Dự án mới'), button:has-text('Tạo dự án'), button:has-text('New project'), button:has-text('Blank project')"
                await self.page.wait_for_selector(new_btn_selector, state="attached", timeout=20000)

                # Google Flow đổi UI — cần click nhiều lần mới vào Canvas
                for click_attempt in range(5):
                    await self.dismiss_popups()
                    new_btn = self.page.locator(new_btn_selector).first
                    print(f"✨ Click nút [Dự án mới] lần {click_attempt+1}...")
                    await new_btn.click(force=True)
                    await asyncio.sleep(3)

                    # Kiểm tra đã vào Canvas chưa
                    try:
                        await self.page.wait_for_selector("input[type='file']", state="attached", timeout=8000)
                        print("✅ Đã bung Canvas thành công!")
                        break
                    except Exception:
                        # Chưa vào — có thể hiện popup/dialog, thử click tiếp
                        # Thử bấm các nút confirm/ok nếu có
                        for confirm_sel in ["button:has-text('Tạo')", "button:has-text('Create')", "button:has-text('OK')", "button:has-text('Bắt đầu')", "button:has-text('Start')"]:
                            try:
                                confirm_btn = self.page.locator(confirm_sel).first
                                if await confirm_btn.count() > 0:
                                    await confirm_btn.click(force=True)
                                    print(f"   ↳ Bấm thêm nút [{confirm_sel}]")
                                    await asyncio.sleep(2)
                            except Exception:
                                pass
                else:
                    # 5 lần vẫn chưa vào → chụp screenshot debug rồi reload
                    print("⚠️ Click 5 lần chưa vào Canvas!")
                    try:
                        debug_path = f"debug_popup_{self.acc_name}.png"
                        await self.page.screenshot(path=debug_path, full_page=True)
                        print(f"📸 Đã chụp màn hình debug: {debug_path}")
                    except Exception:
                        pass
                    print("🔄 Thử reload...")
                    await self.page.reload(wait_until="domcontentloaded")
                    await asyncio.sleep(5)
                    await self.page.wait_for_selector("input[type='file']", state="attached", timeout=15000)
                    print("✅ Đã vào Canvas sau khi reload!")
            except Exception:
                # Nếu không thấy nút tạo mới + Không có Canvas -> Chắc chắn là chưa đăng nhập!
                print("\n--- 🔒 CHƯA ĐĂNG NHẬP HOẶC MẤT PHIÊN ---")
                if getattr(self, 'headless', False):
                    # Thử kiểm tra có thông báo lỗi Capcha hay chặn nào không
                    title = await self.page.title()
                    print(f"❌ LỖI CHÍ MẠNG: Bot đang chạy ở mô hình Tàng Hình. Tiêu đề Web hiện tại: {title}")
                    print(f"👉 Vui lòng Tắt luồng này, gõ lệnh mở Chrome CÓ MẶT để xác thực: \n   source venv/bin/activate && python3 main.py --acc {self.acc_name}")
                    raise Exception(f"Fail-Fast (Headless): Lỗi đăng nhập. Title Web = {title}")
                else:
                    print("Bot hiện tại ĐANG ĐÔNG CỨNG (Pause) để chờ bạn Đăng nhập bằng tài khoản Google.")
                    print("👉 KIỂM TRA MÀN HÌNH: Đăng nhập xong, chọn mở 1 Project Trống thì BẤM NÚT 'RESUME' ĐỂ BOT CHẠY TIẾP!")
                    await self.page.pause()

    async def clear_previous_job(self):
        """Chờ nhẹ giữa các job — prompt/ảnh cũ đã được Canvas tự xoá khi tạo ảnh mới"""
        await asyncio.sleep(0.5)


    async def process_image_job(self, row_num, input_image_path, prompt, ratio, qty_per_loop, total_qty, download_reso="2K"):
        """Thực thi Logic lõi tải file + sinh Image-to-Image qua Locator Nội suy Mù.
        input_image_path: str (1 ảnh) hoặc list[str] (nhiều ảnh upload cùng lúc)."""
        print(f"🤖 Con Mắt AI: Bắt đầu chạy {total_qty} ảnh cho Row {row_num}...")

        # Chuẩn hoá input thành list
        if isinstance(input_image_path, str):
            input_image_paths = [input_image_path]
        else:
            input_image_paths = input_image_path

        try:
            print(f"🤖 Bắt đầu rà tìm cửa Upload cho vòng lặp đầu tiên ({len(input_image_paths)} ảnh)...")
            # 1. Rà tìm Khe hở cắm File của giao diện (Bất chấp class gì)
            file_input = self.page.locator("input[type='file']")
            await file_input.wait_for(state="attached", timeout=60000)

            # Chụp ảnh hiện trạng danh sách thẻ img trước khi Upload
            old_img_count_before_up = await self.page.locator("img").count()
            old_srcs = []
            for k in range(old_img_count_before_up):
                src = await self.page.locator("img").nth(k).get_attribute("src")
                if src: old_srcs.append(src)

            # CÓ THỂ GOOGLE LABS CÓ NHIỀU NÚT FILE ẨN (VD: Avatar, Cài đặt). Ta nhồi ảnh vào TẤT CẢ nút File để chắc ăn.
            # Playwright set_input_files hỗ trợ list paths để upload nhiều ảnh cùng lúc
            file_inputs = self.page.locator("input[type='file']")
            input_count = await file_inputs.count()
            for k in range(input_count):
                try:
                    await file_inputs.nth(k).evaluate("el => el.value = ''")
                    await file_inputs.nth(k).set_input_files(input_image_paths)
                except Exception:
                    pass

            print(f"👁️ [AI]: Đã rỉa được lỗ hở Upload! Tải {len(input_image_paths)} ảnh lên thành công.")

            # CHỜ ĐỢI ẢNH TẢI LÊN ĐẠT 100% (KHẮC PHỤC LỖI CLICK SỚM)
            # Chờ lâu hơn để web kịp hiện % (CPU lag khi multi-bot)
            await asyncio.sleep(4)
            progress_bar = self.page.locator("text=/^\\d+%$/")
            wait_up = 0
            while wait_up < 45:
                if await progress_bar.count() > 0:
                    print("⏳ Chờ ảnh gốc upload tải đến 100%...")
                    await asyncio.sleep(2)
                    wait_up += 1
                else:
                    # Chờ thêm 2s nữa để chắc chắn % không xuất hiện muộn
                    await asyncio.sleep(2)
                    if await progress_bar.count() == 0:
                        break
                    wait_up += 1

            print("👁️ [AI]: Đang dùng X-Ray lọc màng Render. Chờ ảnh mới trồi lên Canvas...")
            wait_render = 0
            uploaded_srcs = set()
            expected_count = len(input_image_paths)
            while wait_render < 30: # Quét liên tục trong 60 giây (30 vòng x 2s)
                new_img_count_after_up = await self.page.locator("img").count()
                for k in range(new_img_count_after_up):
                    src = await self.page.locator("img").nth(k).get_attribute("src")
                    if src and src not in old_srcs:
                        uploaded_srcs.add(src)
                if len(uploaded_srcs) >= expected_count: break
                await asyncio.sleep(2)
                wait_render += 1

            if uploaded_srcs:
                print(f"✅ Ố LÀ LA! Bắt trúng {len(uploaded_srcs)} Blob URL của Ảnh Gốc!")
                # Dùng ảnh đầu tiên làm locator chính (cho chip attach)
                first_src = next(iter(uploaded_srcs))
                self.uploaded_img_locator = self.page.locator(f"img[src='{first_src}']").first
            else:
                print("⚠️ Á Đù! Chờ 40s rồi mà Google không chịu nhả Blob Ảnh ra! Có thể Web bị lag.")
                self.uploaded_img_locator = self.page.locator("img").last
                uploaded_srcs = set()

        except Exception as e:
            print(f"🛑 [LỖI TẢI ẢNH GỐC]: \nChi tiết: {e}")
            return []

        downloaded_files = []
        loops = (total_qty // qty_per_loop) + (1 if total_qty % qty_per_loop != 0 else 0)
        
        for i in range(loops):
            print(f"\n======================================")
            print(f"⏳ VÒNG LẶP SINH ẢNH THỨ {i+1}/{loops}...")
            print(f"======================================")
                
            try:
                # -------- CHECK CHIP ẢNH: nếu Flow tự gài → skip, chưa có → gài thủ công --------
                await asyncio.sleep(1.5)
                chip_selector = "img[src^='blob:']"
                all_chips = await self.page.locator(chip_selector).all()
                small_chips = []
                for c in all_chips:
                    try:
                        bb = await c.bounding_box()
                        if bb and bb['width'] < 150 and bb['height'] < 150:
                            small_chips.append(c)
                    except Exception:
                        pass

                if len(small_chips) > 0:
                    print("✅ Ảnh gốc đã tự gài vào prompt (Flow mới).")
                else:
                    print("👁️ Chưa thấy chip ảnh — thử gài thủ công...")
                    for attach_attempt in range(3):
                        try:
                            await self.uploaded_img_locator.hover()
                            await asyncio.sleep(0.5)

                            p_wrap = self.uploaded_img_locator.locator("xpath=./ancestor::div[2]")
                            if await p_wrap.locator("button").count() == 0:
                                p_wrap = self.uploaded_img_locator.locator("xpath=./ancestor::div[3]")
                            if await p_wrap.locator("button").count() == 0:
                                p_wrap = self.uploaded_img_locator.locator("xpath=./ancestor::div[4]")

                            await p_wrap.locator("button").last.hover()
                            await asyncio.sleep(1.5)
                            await p_wrap.locator("button").last.click(force=True)

                            add_btn_selector = "div[role='menuitem']:has-text('Thêm vào'), button:has-text('Thêm vào'), span:has-text('Thêm vào câu lệnh')"
                            try:
                                await self.page.wait_for_selector(add_btn_selector, state="visible", timeout=10000)
                                await self.page.locator(add_btn_selector).last.click(force=True)
                                await asyncio.sleep(1.5)
                                print("✅ Đã gài ảnh gốc vào prompt thủ công!")
                                break
                            except Exception:
                                await self.page.keyboard.press("Escape")
                                if attach_attempt < 2:
                                    print(f"   🔄 Menu chưa hiện (lần {attach_attempt+1}), thử lại...")
                                    await asyncio.sleep(1)
                        except Exception as e:
                            if attach_attempt < 2:
                                print(f"   🔄 Lỗi gài ảnh (lần {attach_attempt+1}): {e}, thử lại...")
                                await asyncio.sleep(1)
                    else:
                        print("⚠️ Không gài được ảnh sau 3 lần — tiếp tục chạy prompt không ảnh")

                # DÁN PROMPT VÀO CUỐI CÙNG
                input_boxes = self.page.locator("textarea[placeholder*='Bạn muốn tạo gì']:visible, [contenteditable='true']:visible, textarea:not(.g-recaptcha-response):visible, input[type='text']:not([type='hidden']):visible")
                active_box = input_boxes.last

                await active_box.scroll_into_view_if_needed()
                await active_box.click(force=True)
                await asyncio.sleep(1) # Đợi nhấp nháy con trỏ
                
                # Để đối phó với việc 4 trình duyệt thi nhau giành RAM và CPU, gõ từng cụm cẩn thận
                
                # CÁCH CHỐNG LŨ BỘ NHỚ LƯU TẠM CỦA GOOGLE LABS:
                # Nếu chạy cùng một prompt nhiều lần liên tiếp, Google sẽ trả về mẻ hình CŨ. Ta phải chèn từ khoá ẩn.
                variations = ["", ", highly detailed", ", masterpiece", ", 4k resolution", ", best quality"]
                var_suffix = variations[i % len(variations)]
                final_prompt = prompt + var_suffix
                
                await active_box.fill("") # Ép reset ô
                await self.page.keyboard.insert_text(final_prompt)
                await self.page.keyboard.press("Space")
                print(f"👁️ [AI]: Đã Dán (Paste) Prompt vào Vòng {i+1} (Dùng hậu tố: '{var_suffix}').")

                # 3. MỞ MENU CẤU HÌNH ĐỂ ÉP TỈ LỆ VÀ SỐ LƯỢNG MẺ
                config_btn = self.page.locator("button:has-text('x1'), button:has-text('x2'), button:has-text('x3'), button:has-text('x4')").last
                if await config_btn.count() > 0:
                    await config_btn.click(force=True)
                    await asyncio.sleep(1) # Chờ popup bay lên
                    ratio_btn = self.page.locator(f"text='{ratio}'").last
                    if await ratio_btn.count() > 0: await ratio_btn.click()
                    qty_btn = self.page.locator(f"text='x{qty_per_loop}'").last
                    if await qty_btn.count() > 0: await qty_btn.click()
                    print(f"✅ Đã cấu hình Ratio {ratio} và số lượng x{qty_per_loop}.")
                    await self.page.keyboard.press("Escape")
                    await asyncio.sleep(1)
                    
            except Exception as e:
                print(f"🛑 [LỖI GIAO DIỆN CHAT]: {e}")
                pass
            
            # Ghi nhớ danh sách ảnh hiện diện lúc CHƯA TẠO (Bắt bằng Link Blob/Ảnh)
            # Khắc phục lỗi: Google sẽ tự biến mất các ảnh cũ khi cuộn trang, làm mất độ chuẩn xác của việc "đếm số tag img"
            old_img_locators = await self.page.locator("img").all()
            old_srcs_before_gen = []
            for m_img in old_img_locators:
                src = await m_img.get_attribute("src")
                if src: old_srcs_before_gen.append(src)
                
            # TUYỆT CHIÊU KẸP NÁCH: Tìm nút Gửi (Mũi Tên)
            # Dựa vào Bức Ảnh của Bạn: Nút Gửi nằm sát CẠNH BÊN PHẢI của nút Cấu hình (x4)
            sent_success = False
            try:
                # Đọc lại nút cấu hình tỷ lệ
                config_btn = self.page.locator("button:has-text('x1'), button:has-text('x2'), button:has-text('x3'), button:has-text('x4')").last
                if await config_btn.count() > 0:
                    toolbar_parent = config_btn.locator("xpath=./..")
                    send_btn = toolbar_parent.locator("button").last
                    await send_btn.click(force=True, timeout=10000)
                    sent_success = True
                    await asyncio.sleep(1)
            except Exception:
                pass
                
            if not sent_success:
                await active_box.click(force=True)
                await asyncio.sleep(0.5)
                await self.page.keyboard.press("Meta+Enter") 
                await self.page.keyboard.press("Control+Enter")
                await self.page.keyboard.press("Enter")

            print("⏳ Bắt đầu ra lệnh Chạy! (Mắt thần đang liên tục dòm xem tiến trình)...")

            await asyncio.sleep(8) # Chờ lâu hơn cho CPU lag khi multi-bot

            # LOGIC CHỜ THÔNG MINH 100%: Dò tìm có nhãn % nào trên màn hình không (Ví dụ: 60%, 99%)
            # Nếu 15s đầu không thấy % → có thể prompt chưa gửi được, thử gửi lại
            wait_time = 0
            ever_saw_progress = False
            while wait_time < 240:
                progress_indicators = self.page.locator("text=/^\\d+%$/")
                if await progress_indicators.count() > 0:
                    ever_saw_progress = True
                    print("⏳ Google đang vẽ vời... đợi full 100%...")
                    await asyncio.sleep(4)
                    wait_time += 4
                else:
                    if not ever_saw_progress and wait_time < 15:
                        # Chưa thấy progress → chờ thêm (CPU lag, prompt chưa gửi kịp)
                        await asyncio.sleep(3)
                        wait_time += 3
                        continue
                    print("🎉 Ố LÀ LA! 100% Hoàn Tất! Các mảng ảnh đã nặn xong!!!")
                    await asyncio.sleep(3) # Chờ cho ảnh render sắc nét
                    break
            else:
                print("⚠️ TIMEOUT: Đã chờ 240s mà progress vẫn chưa xong!")

            # Scroll xuống cuối để ép Google render lazy images
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)

            # 4. Quét Lấy Nút Tải Xuống Ổn Định thông qua Phép thử Lọc Trừ URL Mới/Cũ
            new_img_locators = await self.page.locator("img").all()
            new_target_srcs = []

            for n_img in new_img_locators:
                src = await n_img.get_attribute("src")
                if src and src not in old_srcs_before_gen and src not in uploaded_srcs:
                    if src not in new_target_srcs:
                        new_target_srcs.append(src)

            # Nếu chưa thấy, thử scroll và quét lại 1 lần nữa
            if len(new_target_srcs) == 0:
                print("🔍 Chưa thấy ảnh mới, scroll quét lại...")
                await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(4)
                new_img_locators = await self.page.locator("img").all()
                for n_img in new_img_locators:
                    src = await n_img.get_attribute("src")
                    if src and src not in old_srcs_before_gen and src not in uploaded_srcs:
                        if src not in new_target_srcs:
                            new_target_srcs.append(src)

            if len(new_target_srcs) == 0:
                print("⚠️ Lỗi: Không thấy nhô ra ảnh mới nào! (Canvas bị đóng băng kéo hoặc lag mạng).")
                continue
                
            print(f"🎉 Rà thấy MỚI RA THÊM {len(new_target_srcs)} mảng ảnh thật trơn tru!")
            
            # Giới hạn số lượng tải 
            new_target_srcs = new_target_srcs[:qty_per_loop]
            print(f"👁️ Bắt đầu luồn lách vào 3 Chấm của {len(new_target_srcs)} tấm ảnh mới nhất...")
            
            for j, target_src in enumerate(new_target_srcs):
                try:
                    # Tránh lỗi Node Detached và CSS Escape Limit bằng cách tìm rà theo nth()
                    img_node = None
                    curr_imgs_count = await self.page.locator("img").count()
                    for p in range(curr_imgs_count):
                        loc = self.page.locator("img").nth(p)
                        if await loc.get_attribute("src") == target_src:
                            img_node = loc
                            break
                            
                    if not img_node:
                        print(f"⚠️ Trượt tay: Bất ngờ mất dấu tấm ảnh thứ {j+1} trên bản đồ!")
                        continue
                        
                    # Đóng Menu của ảnh trước một cách an toàn bằng 1 phím Escape (để không cuộn trang)
                    await self.page.keyboard.press("Escape")
                    await asyncio.sleep(0.3)
                    
                    # 🔴 GIẾT CHẾT CÁC POPUP THÔNG BÁO XUẤT HIỆN GÓC PHẢI TRÊN (Che mất hình 4)
                    try:
                        await self.page.evaluate('''() => {
                            document.querySelectorAll('[role="alert"], [role="status"], snack-bar-container, .mat-mdc-snack-bar-container').forEach(el => el.remove());
                        }''')
                        
                        close_toast_btns = self.page.locator("button:has-text('Đóng'), button:has-text('Close')")
                        if await close_toast_btns.count() > 0:
                            for idx in range(await close_toast_btns.count()):
                                await close_toast_btns.nth(idx).click(force=True)
                    except Exception: pass

                    # Tìm tile wrapper chứa ảnh này (dùng data-tile-id)
                    tile = img_node.locator("xpath=./ancestor::div[@data-tile-id][1]")
                    if await tile.count() == 0:
                        print(f"⚠️ Không tìm thấy tile wrapper cho ảnh {j+1}")
                        continue

                    await asyncio.sleep(0.2)

                    # Scroll tile vào viewport trước khi hover (fix ảnh 3,4 bị khuất)
                    await tile.evaluate("el => el.scrollIntoViewIfNeeded()")
                    await asyncio.sleep(0.3)

                    # Hover tile: dùng hover() thường (có scroll) thay vì force=True
                    menu_btn = None
                    for hover_attempt in range(3):
                        await tile.hover()
                        await asyncio.sleep(1.0)

                        # Tìm nút 3 chấm BÊN TRONG tile
                        tile_btns = await tile.locator("button:visible").all()

                        # Fallback: mouse.move vào tâm ảnh nếu chưa thấy nút
                        if len(tile_btns) == 0:
                            img_bb_pre = await img_node.bounding_box()
                            if img_bb_pre:
                                await self.page.mouse.move(img_bb_pre['x'] + img_bb_pre['width']/2, img_bb_pre['y'] + img_bb_pre['height']/2)
                                await asyncio.sleep(1.0)
                                tile_btns = await tile.locator("button:visible").all()

                        if len(tile_btns) > 0:
                            break
                        if hover_attempt < 2:
                            print(f"   🔄 Hover lần {hover_attempt+1} chưa thấy nút, thử lại...")

                    if len(tile_btns) > 0:
                        img_bb = await img_node.bounding_box()
                        if img_bb and len(tile_btns) > 1:
                            # Chọn nút gần góc phải trên nhất
                            target_x = img_bb['x'] + img_bb['width']
                            target_y = img_bb['y']
                            min_dist = 999999
                            for b in tile_btns:
                                b_bb = await b.bounding_box()
                                if not b_bb or b_bb['width'] > 60 or b_bb['height'] > 60: continue
                                btn_cx = b_bb['x'] + b_bb['width'] / 2
                                btn_cy = b_bb['y'] + b_bb['height'] / 2
                                dist = (btn_cx - target_x)**2 + (btn_cy - target_y)**2
                                if dist < min_dist:
                                    min_dist = dist
                                    menu_btn = b
                        else:
                            menu_btn = tile_btns[-1]
                    
                    if menu_btn:
                        try:
                            # Chống treo Playwright 30s với Locator
                            await menu_btn.click(force=True, timeout=10000)
                        except Exception:
                            await menu_btn.evaluate("el => el.click()")
                        await asyncio.sleep(1.2)

                        # Không được dùng "text='Tải xuống'" có ngoặc đơn do Google nhúng icon
                        dl_btn = self.page.locator("div[role='menuitem']:has-text('Tải xuống'):visible, div[role='menuitem']:has-text('Download'):visible, span:has-text('Tải xuống'):visible").last

                        # Fix lỗi "Mù mắt": Google bị lú Menu, bấm lại phát nữa cho rơi Menu ra!
                        if await dl_btn.count() == 0:
                            await self.page.keyboard.press("Escape")
                            await asyncio.sleep(0.3)
                            await menu_btn.click(force=True)
                            await asyncio.sleep(1.2)
                            dl_btn = self.page.locator("div[role='menuitem']:has-text('Tải xuống'):visible, div[role='menuitem']:has-text('Download'):visible, span:has-text('Tải xuống'):visible").last
                        
                        if await dl_btn.count() > 0:
                            await dl_btn.hover(force=True)
                            await asyncio.sleep(0.8)

                            reso_btn = self.page.locator(f"div[role='menuitem']:has-text('{download_reso}'):visible, span:has-text('{download_reso}'):visible").last
                            if await reso_btn.count() > 0:
                                async with self.page.expect_download(timeout=120000) as download_info:
                                    try:
                                        await reso_btn.click(force=True, timeout=10000)
                                    except Exception:
                                        # Ép nhấp bằng JS nếu React chặn
                                        await reso_btn.evaluate("el => el.click()")
                                print(f"✅ Đã bấm dính nút chọn mốc [{download_reso}] hoàn hảo!")
                            else:
                                async with self.page.expect_download(timeout=120000) as download_info:
                                    try:
                                        await dl_btn.click(force=True, timeout=10000)
                                    except Exception:
                                        await dl_btn.evaluate("el => el.click()")

                            download = await download_info.value
                            
                            original_filename = download.suggested_filename
                            ext = os.path.splitext(original_filename)[1]
                            if not ext or ext.lower() not in ['.png', '.jpg', '.jpeg', '.webp']: 
                                ext = ".png"

                            seq_num = len(downloaded_files) + 1
                            # Đặt tên mượt mà theo cấu trúc [Row]_[Số thứ tự] để tránh đụng hàng giữa các Row
                            final_name = f"{row_num}_{seq_num}{ext}"
                            save_path = os.path.join(self.download_dir, final_name)
                            await download.save_as(save_path)
                            downloaded_files.append(save_path)
                            print(f"📥 Đã hứng thành công ảnh {seq_num}/{total_qty}: {save_path}")

                            await self.page.keyboard.press("Escape")
                            await asyncio.sleep(0.3)

                            # Reset scroll sau download để tránh lệch vị trí ảnh tiếp theo
                            await self.page.evaluate("""() => {
                                document.querySelectorAll('*').forEach(el => {
                                    if (el.scrollTop > 0) el.scrollTop = 0;
                                });
                            }""")
                        else:
                            print("⚠️ Mở menu rồi mà mù mắt ko thấy chữ Tải xuống!")
                            await self.page.keyboard.press("Escape")
                    else:
                        print("⚠️ Lỗi trèo cấp: Thẻ khoanh vùng ảnh không mọc ra nút 3 chấm!")
                except Exception as e:
                    print(f"⚠️ Trượt tay Download ảnh thứ {j+1}: {e}")

        return downloaded_files

    async def close(self):
        await self.browser.close()
        await self.playwright.stop()
