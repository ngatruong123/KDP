import os
import asyncio
import argparse
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
        print(f"❌ Lỗi khởi tạo API: {e}")
        return

    # 2. Mở trình duyệt
    bot = ImageBotCore(acc_name=args.acc, headless=args.headless)
    await bot.init_browser()
    await bot.check_login_and_navigate()

    input_dir = f"inputs_temp_{args.acc}" if args.acc != "default" else "inputs_temp"
    os.makedirs(input_dir, exist_ok=True)

    # 3. Quét reset các dòng lỗi để bot tự chạy lại
    print("🔄 Đang quét dòng lỗi để reset...")
    gmanager.reset_failed_jobs()

    # 4. VÒNG LẶP SĂN VIỆC LIÊN TỤC (có auto-retry khi hết việc)
    MAX_ROUNDS = 3  # Tối đa 3 vòng quét lại lỗi
    current_round = 0
    job_idx = 0
    while True:
        job = gmanager.checkout_next_job(args.acc)
        if not job:
            # Hết việc → quét xem còn dòng lỗi nào không, nếu có thì reset và chạy lại
            current_round += 1
            if current_round <= MAX_ROUNDS:
                reset_count = gmanager.reset_failed_jobs()
                if reset_count > 0:
                    print(f"🔄 VÒNG QUÉT LẠI {current_round}/{MAX_ROUNDS}: Tìm thấy {reset_count} dòng lỗi, đang chạy lại...")
                    continue
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
                retry_count = job.get('retry_count', 0)
                if retry_count < 5:
                    gmanager.update_job_status(row_num, f"Lỗi Web ❌ Chờ xử lý (thử lại {retry_count + 1})")
                    print(f"⚠️ Cảnh báo: Bot văng lỗi (lần {retry_count + 1}/5). Đã thả lại dòng chờ thử lại.")
                else:
                    gmanager.update_job_status(row_num, "Lỗi Web Vĩnh Viễn ❌", result_link="Không có ảnh nào tải xuống")
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

            # Upload ảnh upscaled (đã đè lên file gốc) ra folder chính
            print(f"📦 Đang upload {len(output_files_paths)} ảnh upscaled...")
            for file_path in output_files_paths:
                if os.path.exists(file_path):
                    gmanager.upload_file_to_drive(file_path, os.path.basename(file_path), job_specific_folder_id)

            if _skip_bg_removal:
                # --no-cut: chỉ upload upscaled, không cần _Processed
                link_share = f"https://drive.google.com/drive/folders/{job_specific_folder_id}"
            else:
                # Tìm hoặc tạo subfolder _Processed
                query = f"'{job_specific_folder_id}' in parents and name='_Processed' and mimeType='application/vnd.google-apps.folder' and trashed=false"
                existing = gmanager.drive_service.files().list(q=query, fields="files(id)").execute().get('files', [])
                if existing:
                    processed_folder_id = existing[0]['id']
                else:
                    processed_folder_id, _ = gmanager.create_drive_folder("_Processed", job_specific_folder_id)

                # Upload ảnh đã cắt nền vào _Processed
                for final_upload_path in processed_paths:
                    print(f"✅ Đang xách ảnh {os.path.basename(final_upload_path)} đưa lên Mây...")
                    gmanager.upload_file_to_drive(final_upload_path, os.path.basename(final_upload_path), processed_folder_id)
                link_share = f"https://drive.google.com/drive/folders/{processed_folder_id}"

            # Cập nhật kết quả
            if len(processed_paths) > 0:
                gmanager.update_job_status(row_num, "Xong ✅", result_link=link_share)
                print(f"🎉 HOÀN TẤT! {len(processed_paths)} ảnh đã lên Drive. Link: {link_share}")
            else:
                gmanager.update_job_status(row_num, "Lỗi Xử Lý Ảnh ❌", result_link="Không có ảnh nào xử lý thành công")
                print(f"❌ TOÀN BỘ ảnh xử lý thất bại cho dòng {row_num}")

            # Cleanup temp files
            all_temps = output_files_paths + processed_paths
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
