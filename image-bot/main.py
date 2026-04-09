import os
import asyncio
import argparse
from google_api import GoogleManager
from bot import ImageBotCore
from kdp_local_bot import process_single_image
from concurrent.futures import ThreadPoolExecutor

def _process_one(args):
    """Xử lý 1 ảnh (dùng cho ThreadPoolExecutor)"""
    file_path, processed_path = args
    print(f"🔪 Đang xử lý: {os.path.basename(file_path)}...")
    process_single_image(file_path, processed_path)
    return processed_path

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

    # 3. Quét reset các dòng lỗi để bot tự chạy lại
    print("🔄 Đang quét dòng lỗi để reset...")
    gmanager.reset_failed_jobs()

    # 4. VÒNG LẶP SĂN VIỆC LIÊN TỤC
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
                if retry_count < 2:
                    gmanager.update_job_status(row_num, f"Lỗi Web ❌ Chờ xử lý (thử lại {retry_count + 1})")
                    print(f"⚠️ Cảnh báo: Bot văng lỗi. Đã thả lại dòng này vào Lưới chờ để các Bot khác (hoặc chính nó) thử lại ở vòng sau.")
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

            # Upload ảnh raw ra ngoài folder chính
            print(f"📦 Đang upload {len(output_files_paths)} ảnh raw...")
            for file_path in output_files_paths:
                gmanager.upload_file_to_drive(file_path, os.path.basename(file_path), job_specific_folder_id)

            # Tìm hoặc tạo subfolder _Processed (chỉ 1 folder duy nhất)
            query = f"'{job_specific_folder_id}' in parents and name='_Processed' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            existing = gmanager.drive_service.files().list(q=query, fields="files(id)").execute().get('files', [])
            if existing:
                processed_folder_id = existing[0]['id']
            else:
                processed_folder_id, _ = gmanager.create_drive_folder("_Processed", job_specific_folder_id)

            # Xử lý song song: upscale + tách nền (2 process)
            process_args = [(fp, fp.rsplit('.', 1)[0] + '_VIP.png') for fp in output_files_paths]
            print(f"⚡ Xử lý song song {len(output_files_paths)} ảnh (2 thread)...")
            with ThreadPoolExecutor(max_workers=2) as executor:
                processed_paths = list(executor.map(_process_one, process_args))

            # Upload ảnh đã xử lý vào _Processed
            for final_upload_path in processed_paths:
                print(f"✅ Đang xách ảnh {os.path.basename(final_upload_path)} đưa lên Mây...")
                gmanager.upload_file_to_drive(final_upload_path, os.path.basename(final_upload_path), processed_folder_id)

            # Link sheet trỏ vào _Processed
            link_share = f"https://drive.google.com/drive/folders/{processed_folder_id}"

            # Cập nhật kết quả vào Excel
            gmanager.update_job_status(row_num, "Xong ✅", result_link=link_share)
            print(f"🎉 SUẤT XẮC! Ảnh ĐÃ VÀO DRIVE THẬT 100%. Link tải: {link_share}")

            # Cleanup temp files
            for f in output_files_paths + processed_paths:
                if os.path.exists(f):
                    os.remove(f)
            if os.path.exists(input_path):
                os.remove(input_path)
            print("🧹 Đã dọn sạch file tạm.")

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
