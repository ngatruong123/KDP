import os
import asyncio
import argparse
from google_api import GoogleManager
from bot import ImageBotCore
import shutil

async def main():
    parser = argparse.ArgumentParser(description="Image Bot Queue Worker")
    parser.add_argument("--acc", type=str, default="default", help="Tên tài khoản (Ví dụ: acc1, acc2)")
    parser.add_argument("--headless", action="store_true", help="Chạy ẩn (không mở giao diện Chrome)")
    args = parser.parse_args()

    print(f"🌟=== KHỞI ĐỘNG CÔNG NHÂN BOT (Account: {args.acc}) ===🌟")
    
    # 1. Kết nối Google API
    try:
        gmanager = GoogleManager()
    except Exception as e:
        print(f"❌ Lỗi khởi tạo API: {e}")
        return

    # 2. Mở trình duyệt
    bot = ImageBotCore(acc_name=args.acc, headless=args.headless)
    await bot.init_browser()
    await bot.check_login_and_navigate()

    input_dir = f"inputs_temp_{args.acc}" if args.acc != "default" else "inputs_temp"
    os.makedirs(input_dir, exist_ok=True)

    # 3. VÒNG LẶP SĂN VIỆC LIÊN TỤC
    job_idx = 0
    while True:
        job = gmanager.checkout_next_job(args.acc)
        if not job:
            print("🎉 Hết việc trên lưới! Không còn dòng nào 'Chờ xử lý'. Bot xin phép về chuồng ngủ!")
            break

        row_num = job['row_num']
        id_goc = job['id_anh_goc']
        prompt = job['prompt']
        so_luong = job['so_luong']
        tong_so_luong = job['tong_so_luong']
        download_reso = job['download_reso']
        aspect_ratio = job['aspect_ratio']
        
        print(f"\n======================================")
        print(f"⚙️ NHẬN LỆNH DÒNG {row_num} (ID ẢNH: {id_goc[:8]}...)")

        # Xoá phiên cũ
        if job_idx > 0:
            await bot.clear_previous_job()
        
        job_idx += 1

        # Chuẩn bị dọn rác
        input_path = f"{input_dir}/temp_input_{row_num}.jpg"
        if os.path.exists(input_path):
            os.remove(input_path)

        # Trích tải ảnh từ G-Drive của bạn xuống ổ cứng
        success = gmanager.download_file_from_drive(id_goc, input_path)
        if not success:
            gmanager.update_job_status(row_num, "Lỗi Tải GDrive ❌")
            continue

        try:
            # ---> BƯỚC NÀY ĐANG ĐƯỢC ĐẶT Ở CHẾ ĐỘ RÀ SOÁT BẢO TRÌ (DEBUG MODE)
            # Bởi vì các thao tác ấn click chuột của Playwright lên thẳng Web của Google
            # Cần căn chỉnh trực tiếp vị trí nên script bot.process_image_job đang được Comment lại ở file bot.py

            # GỌI LOGIC TẠO ẢNH BẰNG TRÌNH DUYỆT GHÉP NỐI VỚI GOOGLE LABS
            download_reso = job.get('download_reso', '2K')
            print(f"🚀 Bắt chuyển giao lệnh cho Bot ({tong_so_luong} ảnh, độ nét y/c: {download_reso})...")
            output_files_paths = await bot.process_image_job(job_idx + 1, input_path, prompt, job['aspect_ratio'], so_luong, tong_so_luong, download_reso)
            
            if not output_files_paths:
                retry_count = job.get('retry_count', 0)
                if retry_count < 2:
                    gmanager.update_job_status(row_num, f"Lỗi Web ❌ Chờ xử lý (thử lại {retry_count + 1})")
                    print(f"⚠️ Cảnh báo: Bot văng lỗi. Đã thả lại dòng này vào Lưới chờ để các Bot khác (hoặc chính nó) thử lại ở vòng sau.")
                else:
                    gmanager.update_job_status(row_num, "Lỗi Web Vĩnh Viễn ❌", result_link="Không có ảnh nào tải xuống")
                continue

            # NẾU CÓ ẢNH THẬT ĐƯỢC TẢI VỀ THÀNH CÔNG TỪ BOT: Khởi tạo Drive
            output_folder_id = "1yKY-v8gU8O0hbnS4XJc5GU1L2U3oMpKR"
            link_share = f"https://drive.google.com/drive/folders/{output_folder_id}"
            
            # --- START AI BACKGROUND REMOVAL ---
            # Vòng lặp bắn từng ảnh thật lên Drive
            for file_path in output_files_paths:
                final_upload_path = file_path
                try:
                    # Chạy Lò Cắt Nền AI
                    print(f"✂️ Đang ném tấm [ {os.path.basename(file_path)} ] vào Lò AI bóc nền trong suốt...")
                    import cv2
                    import cv2
                    import numpy as np
                    from PIL import Image
                    from collections import Counter
                    
                    input_img = cv2.imread(file_path)
                    if input_img is None: raise Exception("Failed to read image with OpenCV")
                    
                    # 1. Detect background color from image borders
                    top = input_img[0, :]
                    bottom = input_img[-1, :]
                    left = input_img[:, 0]
                    right = input_img[:, -1]
                    
                    border_pixels = np.vstack((top, bottom, left, right))
                    colors, counts = np.unique(border_pixels, axis=0, return_counts=True)
                    bg_bgr = colors[np.argmax(counts)] 
                    
                    # 2. Extract base Hue, Saturation, Value in HSV color space
                    hsv = cv2.cvtColor(input_img, cv2.COLOR_BGR2HSV)
                    bg_bgr_1px = np.uint8([[bg_bgr]])
                    bg_hsv = cv2.cvtColor(bg_bgr_1px, cv2.COLOR_BGR2HSV)[0][0]
                    hue_goc = int(bg_hsv[0])
                    sat_goc = int(bg_hsv[1])
                    val_goc = int(bg_hsv[2])
                    
                    # 3. Create global HSV mask covering hue variations and shadow gradients
                    # Handle Achromatic (Black/White) independently from Chromatic (Colored)
                    if val_goc < 40: # Pure Black background
                        lower_bound = np.array([0, 0, 0])
                        upper_bound = np.array([179, 255, val_goc + 40])
                    elif sat_goc < 40 and val_goc > 200: # Pure White background
                        lower_bound = np.array([0, 0, val_goc - 40])
                        upper_bound = np.array([179, sat_goc + 40, 255])
                    else: # Colored background
                        lower_bound = np.array([max(0, hue_goc - 25), 20, 20])
                        upper_bound = np.array([min(179, hue_goc + 25), 255, 255])
                        
                    mask_bg = cv2.inRange(hsv, lower_bound, upper_bound)
                    
                    # Apply morphological close to patch minor noise
                    kernel = np.ones((3,3), np.uint8)
                    mask_closed = cv2.morphologyEx(mask_bg, cv2.MORPH_CLOSE, kernel)
                    
                    # 4. Topological Separation: Isolate outer boundary from inner holes
                    num_labels, labels = cv2.connectedComponents(mask_closed)
                    unique_labels, count_labels = np.unique(labels, return_counts=True)
                    
                    max_label = 0
                    max_count = 0
                    for lab, cnt in zip(unique_labels, count_labels):
                        if lab == 0: continue # Skip the foreground
                        if cnt > max_count:
                            max_count = cnt
                            max_label = lab
                    
                    mask_outside = np.zeros_like(mask_closed)
                    if max_label > 0:
                        mask_outside[labels == max_label] = 255
                        
                    mask_inside = cv2.bitwise_xor(mask_closed, mask_outside)
                    
                    # 5. Apply 1px erosion only to the outer boundary
                    mask_outside_core = cv2.dilate(mask_outside, kernel, iterations=1)
                    mask_combined = cv2.bitwise_or(mask_outside_core, mask_inside)
                    
                    # Apply anti-aliasing blur
                    mask_blurred = cv2.GaussianBlur(mask_combined, (3, 3), 0)
                    
                    # 6. Apply dynamic color despill based on background hue
                    b, g, r = cv2.split(input_img)
                    # Only apply Despill if the background is actually colored
                    if val_goc >= 40 and not (sat_goc < 40 and val_goc > 200):
                        if 30 <= hue_goc <= 90:
                            g = np.minimum(g, np.maximum(r, b))
                        elif 90 < hue_goc <= 150:
                            b = np.minimum(b, np.maximum(r, g))
                        else:
                            r = np.minimum(r, np.maximum(b, g))
                    img_despilled = cv2.merge([b, g, r])
                    
                    # 7. Apply alpha mask
                    img_bgra = cv2.cvtColor(img_despilled, cv2.COLOR_BGR2BGRA)
                    img_bgra[:,:,3] = 255 - mask_blurred
                    
                    # 8. Upscale to 2x
                    h_cu, w_cu = img_bgra.shape[:2]
                    img_upscaled = cv2.resize(img_bgra, (w_cu * 2, h_cu * 2), interpolation=cv2.INTER_LANCZOS4)
                    
                    # 9. Ultra-Aggressive Convolution Sharpening Matrix (Maximum Acutance)
                    # Split channels to protect the flawless vector Alpha from halo-ringing
                    b_up, g_up, r_up, a_up = cv2.split(img_upscaled)
                    bgr_upscaled = cv2.merge([b_up, g_up, r_up])
                    
                    # 3x3 High-Pass Filter Kernel (Deep-fry edge separation)
                    kernel_sharpen = np.array([[-1, -1, -1], 
                                               [-1,  9, -1], 
                                               [-1, -1, -1]])
                    sharpened_bgr = cv2.filter2D(bgr_upscaled, -1, kernel_sharpen)
                    
                    # Merge sharpened image back with the untouched perfect Alpha mask
                    sh_b, sh_g, sh_r = cv2.split(sharpened_bgr)
                    img_final = cv2.merge([sh_b, sh_g, sh_r, a_up])
                    
                    # 10. Set metadata to 300 DPI
                    img_in_an = cv2.cvtColor(img_final, cv2.COLOR_BGRA2RGBA)
                    pil_img = Image.fromarray(img_in_an)
                    
                    final_upload_path = file_path.rsplit('.', 1)[0] + '_transparent.png'
                    pil_img.save(final_upload_path, "PNG", dpi=(300, 300))
                    print(f"✨ AI Tách Tầng Không Gian x Gọt Chu Vi x Auto Despill x 300DPI! Đã lưu {os.path.basename(final_upload_path)}")
                except Exception as ai_e:
                    print(f"⚠️ Lõi bóc nền vấp đạn, lấy ảnh gốc xài tạm cho nhanh: {ai_e}")

                print(f"✅ Đang xách ảnh {os.path.basename(final_upload_path)} đưa lên Mây (ID: {output_folder_id})...")
                gmanager.upload_file_to_drive(final_upload_path, os.path.basename(final_upload_path), output_folder_id)

            # Cập nhật kết quả vào Excel
            gmanager.update_job_status(row_num, "Xong ✅", result_link=link_share)
            print(f"🎉 SUẤT XẮC! Ảnh ĐÃ VÀO DRIVE THẬT 100%. Link tải: {link_share}")

        except Exception as e:
            print(f"Lỗi khi chạy vòng lặp Bot: {e}")
            retry_count = job.get('retry_count', 0)
            if retry_count < 2:
                gmanager.update_job_status(row_num, f"Lỗi văng App ❌ Chờ xử lý (thử lại {retry_count + 1})")
            else:
                gmanager.update_job_status(row_num, "Lỗi Kịch Bản Vĩnh Viễn ❌")

    await bot.close()
    print("\n✅ === HOÀN TẤT NHIỆM VỤ, NGHỈ NGƠI === ✅")

if __name__ == "__main__":
    asyncio.run(main())
