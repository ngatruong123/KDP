import os
import cv2
import time
import subprocess
from PIL import Image
from rembg import remove, new_session
import glob
import numpy as np

# Định nghĩa Tọa Độ của File chạy Upscayl C++
# File này lúc nãy em đã ra lệnh curl tải về thư mục bin/
_engine_name = "realesrgan-ncnn-vulkan.exe" if os.name == "nt" else "realesrgan-ncnn-vulkan"
UPSCAYL_ENGINE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", _engine_name)

# CÁC THƯ MỤC LÀM VIỆC LƯU TRONG THƯ MỤC CODE LUÔN CHO SẠCH
WORKSPACE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kdp_workspace")
THU_MUC_GOC = os.path.join(WORKSPACE_DIR, "0_HinhGoc")
THU_MUC_TAM = os.path.join(WORKSPACE_DIR, "1_DaCatNen")
THU_MUC_UPSCAYLED = os.path.join(WORKSPACE_DIR, "2_DaUpscayl")
THU_MUC_THANH_PHAM = os.path.join(WORKSPACE_DIR, "3_ThanhPham_VIP")

os.makedirs(THU_MUC_GOC, exist_ok=True)
os.makedirs(THU_MUC_TAM, exist_ok=True)
os.makedirs(THU_MUC_UPSCAYLED, exist_ok=True)
os.makedirs(THU_MUC_THANH_PHAM, exist_ok=True)

# Khởi động não Lột Nền
print("🚀 Khởi động Lưỡi Dao Cắt Nền (ISNet - Viền Mượt Không Răng Cưa)...")
try:
    session = new_session("isnet-general-use")
except Exception as e:
    print(f"Lỗi nạp não Rembg: {e}. Vui lòng cài lại qua pip install rembg[gpu] onnxruntime-silicon")
    exit()

if not os.path.exists(UPSCAYL_ENGINE_PATH):
    print("❌ CHƯA TÌM THẤY LÕI CHẠY UPSCAYL!")
    print(f"Vui lòng kiểm tra lại đường dẫn: {UPSCAYL_ENGINE_PATH}")
    exit()

def process_file(ten_file):
    vao = os.path.join(THU_MUC_GOC, ten_file)
    ten_khong_duoi = ten_file.rsplit('.', 1)[0]
    
    tam_path = os.path.join(THU_MUC_TAM, ten_khong_duoi + '_transparent.png')
    esrgan_out = os.path.join(THU_MUC_UPSCAYLED, ten_khong_duoi + '_transparent_out.png')
    ket_qua_path = os.path.join(THU_MUC_THANH_PHAM, ten_khong_duoi + '_VIP.png')
    
    print(f"\n=====================================")
    print(f"🔥 ĐANG CHẶT THỊT TẤM: {ten_file}")
    
    try:
        # --- BƯỚC 1: UPSCAYL X2 (trên ảnh gốc có nền) ---
        print("📈 [1/3] Kích hoạt Động cơ Upscayl C++ Auto X2...")
        cmd = [
            UPSCAYL_ENGINE_PATH,
            '-i', vao,
            '-o', esrgan_out,
            '-n', 'realesr-animevideov3',
            '-s', '2',
            '-t', '0',
            '-f', 'png'
        ]
        result = subprocess.run(cmd, cwd=os.path.dirname(UPSCAYL_ENGINE_PATH))
        if result.returncode != 0:
            print(f"Lỗi văng App Upscayl!")
            print("➡️ Lỗi này có thể do Cạc Đồ Họa Đời Cũ Của Máy Đuối Sức. Sếp chạy lại nhé.")
            return

        if not os.path.exists(esrgan_out):
            print(f"⚠️ Không thấy Cục Output của Upscayl.")
            return

        # --- BƯỚC 2: SHARPEN LAB ---
        print("⚒️ [2/3] Mài Bút L.A.B Chống Đục...")
        img_upscaled = cv2.imread(esrgan_out, cv2.IMREAD_UNCHANGED)

        bgr = img_upscaled[:, :, :3] if img_upscaled.shape[2] >= 3 else img_upscaled
        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
        str_l, str_a, str_b = cv2.split(lab)
        blurred_l = cv2.GaussianBlur(str_l, (0, 0), 3.0)
        sharpened_l = cv2.addWeighted(str_l, 3.0, blurred_l, -2.0, 0)
        merged_lab = cv2.merge([sharpened_l, str_a, str_b])
        img_sharpened = cv2.cvtColor(merged_lab, cv2.COLOR_LAB2BGR)
        cv2.imwrite(tam_path, img_sharpened)

        # --- BƯỚC 3: TÁCH NỀN (rembg + chroma-key) trên ảnh đã nét ---
        print("✂️ [3/3] Đang bóc nền trên ảnh đã nét...")
        img_goc = img_sharpened

        with open(tam_path, 'rb') as i:
            output_data = remove(i.read(), session=session, post_process_mask=False,
                                 alpha_matting=True,
                                 alpha_matting_foreground_threshold=240,
                                 alpha_matting_background_threshold=10,
                                 alpha_matting_erode_size=10)
        rembg_path = os.path.join(THU_MUC_TAM, ten_khong_duoi + '_rembg.png')
        with open(rembg_path, 'wb') as o:
            o.write(output_data)

        # Lấy alpha từ rembg, ghép với RGB đã sharpen
        img_rembg = cv2.imread(rembg_path, cv2.IMREAD_UNCHANGED)
        if img_rembg is not None and img_rembg.shape[2] == 4:
            alpha_rembg = img_rembg[:, :, 3]
            img_result = cv2.merge([img_sharpened[:, :, 0], img_sharpened[:, :, 1], img_sharpened[:, :, 2], alpha_rembg])
        else:
            img_result = img_rembg

        # CHROMA-KEY: detect màu nền + xóa triệt để
        from collections import Counter
        if img_goc is not None and img_result is not None and img_result.shape[2] == 4:
            h_img, w_img = img_goc.shape[:2]
            margin = max(5, min(h_img, w_img) // 20)
            samples = []
            samples.extend(img_goc[0:margin, :, :3].reshape(-1, 3).tolist())
            samples.extend(img_goc[-margin:, :, :3].reshape(-1, 3).tolist())
            samples.extend(img_goc[margin:-margin, 0:margin, :3].reshape(-1, 3).tolist())
            samples.extend(img_goc[margin:-margin, -margin:, :3].reshape(-1, 3).tolist())
            samples_arr = np.array(samples, dtype=np.uint8)
            quantized = (samples_arr // 16) * 16 + 8
            color_counts = Counter([tuple(c) for c in quantized.tolist()])
            dominant_bgr = np.array(color_counts.most_common(1)[0][0], dtype=np.uint8)
            print(f"   🎨 Màu nền phát hiện (BGR): {dominant_bgr} - Chiếm {color_counts.most_common(1)[0][1]}/{len(samples)} pixel viền")

            # HSV chroma-key
            dominant_hsv = cv2.cvtColor(dominant_bgr.reshape(1, 1, 3), cv2.COLOR_BGR2HSV)[0][0]
            img_hsv = cv2.cvtColor(img_result[:, :, :3], cv2.COLOR_BGR2HSV)
            h_tol, s_tol, v_tol = 15, 60, 60
            lower_hsv = np.array([max(0, int(dominant_hsv[0]) - h_tol),
                                  max(0, int(dominant_hsv[1]) - s_tol),
                                  max(0, int(dominant_hsv[2]) - v_tol)], dtype=np.uint8)
            upper_hsv = np.array([min(179, int(dominant_hsv[0]) + h_tol),
                                  min(255, int(dominant_hsv[1]) + s_tol),
                                  min(255, int(dominant_hsv[2]) + v_tol)], dtype=np.uint8)
            mask_hsv = cv2.inRange(img_hsv, lower_hsv, upper_hsv)

            bgr_tol = 50
            lower_bgr = np.clip(dominant_bgr.astype(int) - bgr_tol, 0, 255).astype(np.uint8)
            upper_bgr = np.clip(dominant_bgr.astype(int) + bgr_tol, 0, 255).astype(np.uint8)
            mask_bgr = cv2.inRange(img_result[:, :, :3], lower_bgr, upper_bgr)

            mask_bg = cv2.bitwise_or(mask_hsv, mask_bgr)

            b_c, g_c, r_c, a_c = cv2.split(img_result)
            a_c[mask_bg == 255] = 0
            img_result = cv2.merge([b_c, g_c, r_c, a_c])

        # Save 300dpi
        img_rgba = cv2.cvtColor(img_result, cv2.COLOR_BGRA2RGBA)
        pil_img = Image.fromarray(img_rgba)
        pil_img.save(ket_qua_path, "PNG", dpi=(300, 300))

        # Dọn rác
        os.remove(tam_path)
        os.remove(esrgan_out)
        if os.path.exists(rembg_path):
            os.remove(rembg_path)
        os.remove(vao)

        print(f"🥇 HOÀN TẤT THẦN TỐC TẤM: {ten_file}!")
            
    except Exception as e:
        print(f"❌ Kẹt Trục tại {ten_file} - Lỗi: {e}")

def process_single_image(input_path, output_path):
    """
    Hàm xử lý 1 ảnh: rembg → cạo viền + detect nền → upscale x4 → resize x0.5 + sharpen LAB → save 300dpi.
    Dùng để main.py gọi trực tiếp.
    """
    import tempfile, shutil

    ten_file = os.path.basename(input_path)
    ten_khong_duoi = ten_file.rsplit('.', 1)[0]

    with tempfile.TemporaryDirectory() as tmpdir:
        upscale_input = os.path.join(tmpdir, ten_khong_duoi + '_input.png')
        esrgan_out = os.path.join(tmpdir, ten_khong_duoi + '_input_out.png')
        sharpened_path = os.path.join(tmpdir, ten_khong_duoi + '_sharpened.png')

        print(f"\n=====================================")
        print(f"🔥 ĐANG CHẶT THỊT TẤM: {ten_file}")

        # --- BƯỚC 1: UPSCAYL X4 (trên ảnh gốc có nền) ---
        print("📈 [1/3] Kích hoạt Động cơ Upscayl C++ Auto X4...")
        shutil.copy2(input_path, upscale_input)
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        cmd = [
            UPSCAYL_ENGINE_PATH,
            '-i', upscale_input,
            '-o', esrgan_out,
            '-n', 'realesr-animevideov3',
            '-s', '2',
            '-t', '0',
            '-f', 'png'
        ]
        result = subprocess.run(cmd, cwd=os.path.dirname(UPSCAYL_ENGINE_PATH), env=env)
        if result.returncode != 0:
            print(f"❌ Lỗi Upscayl! Trả về file gốc.")
            shutil.copy2(input_path, output_path)
            return output_path

        if not os.path.exists(esrgan_out):
            print(f"⚠️ Không thấy output Upscayl.")
            shutil.copy2(input_path, output_path)
            return output_path

        # --- BƯỚC 2: SHARPEN LAB ---
        print("⚒️ [2/3] Mài Bút L.A.B Chống Đục...")
        img_upscaled = cv2.imread(esrgan_out, cv2.IMREAD_UNCHANGED)

        # Sharpen trên LAB (chỉ kênh L)
        bgr = img_upscaled[:, :, :3] if img_upscaled.shape[2] >= 3 else img_upscaled
        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
        str_l, str_a, str_b = cv2.split(lab)
        blurred_l = cv2.GaussianBlur(str_l, (0, 0), 3.0)
        sharpened_l = cv2.addWeighted(str_l, 3.0, blurred_l, -2.0, 0)
        merged_lab = cv2.merge([sharpened_l, str_a, str_b])
        img_sharpened = cv2.cvtColor(merged_lab, cv2.COLOR_LAB2BGR)
        cv2.imwrite(sharpened_path, img_sharpened)

        # --- BƯỚC 3: TÁCH NỀN (rembg + chroma-key) trên ảnh đã nét ---
        print("✂️ [3/3] Đang bóc nền trên ảnh đã nét...")
        # Detect màu nền từ ảnh đã sharpen
        img_goc = img_sharpened  # dùng ảnh sharpened làm gốc detect

        with open(sharpened_path, 'rb') as i:
            output_data = remove(i.read(), session=session, post_process_mask=False,
                                 alpha_matting=True,
                                 alpha_matting_foreground_threshold=240,
                                 alpha_matting_background_threshold=10,
                                 alpha_matting_erode_size=10)
        rembg_path = os.path.join(tmpdir, ten_khong_duoi + '_rembg.png')
        with open(rembg_path, 'wb') as o:
            o.write(output_data)

        # Lấy alpha từ rembg, ghép với RGB đã sharpen → giữ nguyên chất lượng màu
        img_rembg = cv2.imread(rembg_path, cv2.IMREAD_UNCHANGED)
        if img_rembg is not None and img_rembg.shape[2] == 4:
            alpha_rembg = img_rembg[:, :, 3]
            img_result = cv2.merge([img_sharpened[:, :, 0], img_sharpened[:, :, 1], img_sharpened[:, :, 2], alpha_rembg])
        else:
            img_result = img_rembg

        # CHROMA-KEY: detect màu nền + xóa triệt để
        from collections import Counter
        if img_goc is not None and img_result is not None and img_result.shape[2] == 4:
            h_img, w_img = img_goc.shape[:2]
            margin = max(5, min(h_img, w_img) // 20)
            samples = []
            samples.extend(img_goc[0:margin, :, :3].reshape(-1, 3).tolist())
            samples.extend(img_goc[-margin:, :, :3].reshape(-1, 3).tolist())
            samples.extend(img_goc[margin:-margin, 0:margin, :3].reshape(-1, 3).tolist())
            samples.extend(img_goc[margin:-margin, -margin:, :3].reshape(-1, 3).tolist())
            samples_arr = np.array(samples, dtype=np.uint8)
            quantized = (samples_arr // 16) * 16 + 8
            color_counts = Counter([tuple(c) for c in quantized.tolist()])
            dominant_bgr = np.array(color_counts.most_common(1)[0][0], dtype=np.uint8)
            print(f"   🎨 Màu nền phát hiện (BGR): {dominant_bgr}")

            # HSV chroma-key
            dominant_hsv = cv2.cvtColor(dominant_bgr.reshape(1, 1, 3), cv2.COLOR_BGR2HSV)[0][0]
            img_hsv = cv2.cvtColor(img_result[:, :, :3], cv2.COLOR_BGR2HSV)
            h_tol, s_tol, v_tol = 15, 60, 60
            lower_hsv = np.array([max(0, int(dominant_hsv[0]) - h_tol),
                                  max(0, int(dominant_hsv[1]) - s_tol),
                                  max(0, int(dominant_hsv[2]) - v_tol)], dtype=np.uint8)
            upper_hsv = np.array([min(179, int(dominant_hsv[0]) + h_tol),
                                  min(255, int(dominant_hsv[1]) + s_tol),
                                  min(255, int(dominant_hsv[2]) + v_tol)], dtype=np.uint8)
            mask_hsv = cv2.inRange(img_hsv, lower_hsv, upper_hsv)

            # BGR backup (±50)
            bgr_tol = 50
            lower_bgr = np.clip(dominant_bgr.astype(int) - bgr_tol, 0, 255).astype(np.uint8)
            upper_bgr = np.clip(dominant_bgr.astype(int) + bgr_tol, 0, 255).astype(np.uint8)
            mask_bgr = cv2.inRange(img_result[:, :, :3], lower_bgr, upper_bgr)

            mask_bg = cv2.bitwise_or(mask_hsv, mask_bgr)

            # Xóa alpha pixel khớp màu nền, giữ nguyên pixel khác
            b_c, g_c, r_c, a_c = cv2.split(img_result)
            a_c[mask_bg == 255] = 0
            img_result = cv2.merge([b_c, g_c, r_c, a_c])

        # Save 300dpi
        img_rgba = cv2.cvtColor(img_result, cv2.COLOR_BGRA2RGBA)
        pil_img = Image.fromarray(img_rgba)
        pil_img.save(output_path, "PNG", dpi=(300, 300))
        print(f"🥇 HOÀN TẤT: {ten_file} → {os.path.basename(output_path)}")
        return output_path

    # Fallback
    shutil.copy2(input_path, output_path)
    return output_path


if __name__ == "__main__":
    print(f"🚀 BOT AUTO LOCAL ĐÃ BẬT. BỎ ẢNH VÀO '{THU_MUC_GOC}' VÀ ĐỢI THẦN KỲ NHÉ.")
    while True:
        danh_sach_file = [f for f in os.listdir(THU_MUC_GOC) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
        if danh_sach_file:
            for file in danh_sach_file:
                process_file(file)
        else:
            # Ngủ 5 giây để không ăn mòn CPU
            time.sleep(5)
