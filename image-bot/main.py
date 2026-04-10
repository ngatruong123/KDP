import os
import asyncio
import argparse
import random
from google_api import GoogleManager
from bot import ImageBotCore
from kdp_local_bot import process_single_image

_skip_bg_removal = False
from concurrent.futures import ThreadPoolExecutor

def _process_one(args):
    """Xử lý 1 ảnh (dùng cho ThreadPoolExecutor)"""
    file_path, processed_path = args
    print(f"🔪 Đang xử lý: {os.path.basename(file_path)}...")
    result = process_single_image(file_path, processed_path, skip_bg_removal=_skip_bg_removal)
    return result  # None nếu thất bại

async def main():
    parser = argparse.ArgumentParser(description="Image Bot Queue Worker")
    parser.add_argument("--acc", type=str, default="default", help="Tên tài khoản (Ví dụ: acc1, acc2)")
    parser.add_argument("--headless", action="store_true", help="Chạy ẩn (không mở giao diện Chrome)")
    parser.add_argument("--no-cut", action="store_true", help="Chỉ upscale, không cắt nền")
    args = parser.parse_args()

    global _skip_bg_removal
    _skip_bg_removal = args.no_cut
    mode = "CHỈ UPSCALE" if args.no_cut else "UPSCALE + CẮT NỀN"
    print(f"🌟=== KHỞI ĐỘNG CÔNG NHÂN BOT (Account: {args.acc}, Mode: {mode}) ===🌟")
    
    # 1. Kết nối Google API
    try:
        gmanager = GoogleManager()
    except Exception as e:
        import traceback
        print(f"❌ Lỗi khởi tạo API: {e}")
        traceback.print_exc()
        return

    # 2. Mở trình duyệt
    bot = ImageBotCore(acc_name=args.acc, headless=args.headless)
    await bot.init_browser()
    await bot.check_login_and_navigate()

    input_dir = f"inputs_temp_{args.acc}" if args.acc != "default" else "inputs_temp"
    os.makedirs(input_dir, exist_ok=True)

    # 4. VÒNG LẶP SĂN VIỆC LIÊN TỤC (có auto-retry khi hết việc)
    MAX_ROUNDS = 3
    current_round = 0
    job_idx = 0
    consecutive_web_errors = 0
    first_run = True  # Lần đầu reset lỗi luôn trong checkout (tiết kiệm 1 lần tải sheet)
    while True:
        if job_idx > 0:
            delay = random.uniform(1, 4)
            await asyncio.sleep(delay)

        job = gmanager.checkout_next_job(args.acc, reset_errors=first_run)
        first_run = False
        if not job:
            current_round += 1
            if current_round <= MAX_ROUNDS:
                # Dùng checkout_next_job với reset_errors=True để gộp reset + tìm job
                job = gmanager.checkout_next_job(args.acc, reset_errors=True)
                if job:
                    print(f"🔄 VÒNG QUÉT LẠI {current_round}/{MAX_ROUNDS}: Tìm thấy dòng lỗi, đang chạy lại...")
                else:
                    continue
            if not job:
                print("🎉 Hết việc trên lưới! Không còn dòng nào 'Chờ xử lý'. Bot xin phép về chuồng ngủ!")
                break

        row_num = job['row_num']
        id_goc = job['id_anh_goc']
        prompt = job['prompt']
        so_luong = job['so_luong']
        tong_so_luong = job['tong_so_luong']
        download_reso = job['download_reso']
        
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
            print(f"🚀 Bắt chuyển giao lệnh cho Bot ({tong_so_luong} ảnh, độ nét y/c: {download_reso})...")
            output_files_paths = await bot.process_image_job(row_num, input_path, prompt, job['aspect_ratio'], so_luong, tong_so_luong, download_reso)
            
            if not output_files_paths:
                consecutive_web_errors += 1
                retry_count = job.get('retry_count', 0)
                if retry_count < 5:
                    gmanager.update_job_status(row_num, f"Lỗi Web ❌ Chờ xử lý (thử lại {retry_count + 1})")
                    print(f"⚠️ Cảnh báo: Bot văng lỗi (lần {retry_count + 1}/5). Đã thả lại dòng chờ thử lại.")
                else:
                    gmanager.update_job_status(row_num, "Lỗi Web Vĩnh Viễn ❌", result_link="Không có ảnh nào tải xuống")

                # Nếu lỗi web 3 lần liên tiếp → refresh trang để phục hồi session
                if consecutive_web_errors >= 3:
                    print("🔄 Lỗi web 3 lần liên tiếp! Đang refresh trình duyệt...")
                    try:
                        await bot.page.reload(wait_until="domcontentloaded", timeout=30000)
                        await asyncio.sleep(3)
                        await bot.check_login_and_navigate()
                        consecutive_web_errors = 0
                        print("✅ Refresh thành công! Tiếp tục chạy.")
                    except Exception as e:
                        print(f"❌ Refresh thất bại: {e}")
                continue

            # KHỞI TẠO ĐỊNH TUYẾN: CHUẨN BỊ LƯU TRỮ VÀO GOOGLE DRIVE
            base_output_folder_id = "1yKY-v8gU8O0hbnS4XJc5GU1L2U3oMpKR"
            
            # Khởi chạy Thuật toán Truy vết Nguồn gốc Tự động tạo thư mục Out
            # Ví dụ: Ảnh có nguồn từ Folder "Meo_KDP" => Xây ngay Thư mục "Meo_KDP_Out"
            print(f"🔍 Đang truy vết Nguồn gốc của ảnh để phân loại thư mục...")
            job_specific_folder_id = gmanager.resolve_output_folder(id_goc, base_output_folder_id)
            
            link_share = f"https://drive.google.com/drive/folders/{job_specific_folder_id}"

            # Xử lý song song: upscale + tách nền (2 thread)
            # process_single_image sẽ đè bản upscaled lên file gốc
            process_args = [(fp, fp.rsplit('.', 1)[0] + '_VIP.png') for fp in output_files_paths]
            print(f"⚡ Xử lý song song {len(output_files_paths)} ảnh (2 thread)...")
            with ThreadPoolExecutor(max_workers=2) as executor:
                raw_results = list(executor.map(_process_one, process_args))

            # Lọc bỏ file thất bại (None) — KHÔNG upload ảnh chưa cắt nền
            processed_paths = [p for p in raw_results if p is not None and os.path.exists(p)]
            failed_count = len(raw_results) - len(processed_paths)
            if failed_count > 0:
                print(f"⚠️ {failed_count}/{len(raw_results)} ảnh xử lý THẤT BẠI — bỏ qua, KHÔNG upload ảnh lỗi")

            # Cập nhật path upscaled: process_single_image đổi sang .jpg
            upscaled_paths = []
            for fp in output_files_paths:
                jpg_path = fp.rsplit('.', 1)[0] + '.jpg'
                if os.path.exists(jpg_path):
                    upscaled_paths.append(jpg_path)
                elif os.path.exists(fp):
                    upscaled_paths.append(fp)

            # Upload ảnh upscaled vào subfolder _1.1, _1.2, ... (mỗi ảnh 1 folder)
            print(f"📦 Đang upload {len(upscaled_paths)} ảnh upscaled vào các subfolder...")
            sub_folder_ids = gmanager.get_or_create_numbered_subfolders(job_specific_folder_id, len(output_files_paths))
            for idx, file_path in enumerate(upscaled_paths):
                folder_id = sub_folder_ids[idx] if idx < len(sub_folder_ids) else job_specific_folder_id
                gmanager.upload_file_to_drive(file_path, os.path.basename(file_path), folder_id)

            if _skip_bg_removal:
                link_share = f"https://drive.google.com/drive/folders/{job_specific_folder_id}"
            else:
                # Upload ảnh đã cắt nền vào subfolder _1.1/_Processed, _1.2/_Processed, ...
                for idx, final_upload_path in enumerate(processed_paths):
                    parent_id = sub_folder_ids[idx] if idx < len(sub_folder_ids) else job_specific_folder_id
                    proc_folder_id = gmanager.get_or_create_subfolder("_Processed", parent_id)
                    print(f"✅ Đang xách ảnh {os.path.basename(final_upload_path)} đưa lên Mây...")
                    gmanager.upload_file_to_drive(final_upload_path, os.path.basename(final_upload_path), proc_folder_id)
                link_share = f"https://drive.google.com/drive/folders/{job_specific_folder_id}"

            # Cập nhật kết quả
            consecutive_web_errors = 0  # Reset khi job thành công
            total_uploaded = len(upscaled_paths) + len(processed_paths)
            if total_uploaded > 0:
                gmanager.update_job_status(row_num, "Xong ✅", result_link=link_share)
                print(f"🎉 HOÀN TẤT! {len(upscaled_paths)} upscaled + {len(processed_paths)} processed đã lên Drive.")
            else:
                gmanager.update_job_status(row_num, "Lỗi Xử Lý Ảnh ❌", result_link="Không có ảnh nào xử lý thành công")
                print(f"❌ TOÀN BỘ ảnh xử lý thất bại cho dòng {row_num}")

            # Cleanup temp files
            all_temps = output_files_paths + upscaled_paths + processed_paths
            for f in all_temps:
                if f and os.path.exists(f):
                    os.remove(f)
            if os.path.exists(input_path):
                os.remove(input_path)
            print("🧹 Đã dọn sạch file tạm.")

        except Exception as e:
            import traceback
            err_msg = traceback.format_exc()
            print(f"❌❌❌ LỖI BOT: {e}")
            print(err_msg)
            with open("error_log.txt", "a", encoding="utf-8") as f:
                f.write(f"\n{'='*50}\nDòng {row_num}: {e}\n{err_msg}\n")
            retry_count = job.get('retry_count', 0)
            if retry_count < 5:
                gmanager.update_job_status(row_num, f"Lỗi văng App ❌ Chờ xử lý (thử lại {retry_count + 1})")
            else:
                gmanager.update_job_status(row_num, "Lỗi Kịch Bản Vĩnh Viễn ❌")

    await bot.close()
    print("\n✅ === HOÀN TẤT NHIỆM VỤ, NGHỈ NGƠI === ✅")

if __name__ == "__main__":
    asyncio.run(main())
