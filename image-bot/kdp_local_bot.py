import os
import cv2
import time
import subprocess
from PIL import Image
from rembg import remove, new_session
import glob
import numpy as np
from collections import Counter

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
    raise RuntimeError(f"Lỗi nạp não Rembg: {e}. Vui lòng cài lại qua pip install rembg[gpu] onnxruntime-silicon")

if not os.path.exists(UPSCAYL_ENGINE_PATH):
    raise RuntimeError(f"CHƯA TÌM THẤY LÕI CHẠY UPSCAYL! Vui lòng kiểm tra lại đường dẫn: {UPSCAYL_ENGINE_PATH}")


def _detect_and_remove_chroma(img_goc, img_result):
    """Detect màu nền dominant từ viền ảnh gốc, tạo mask và xoá khỏi alpha channel.
    Trả về img_result đã clean."""
    if img_goc is None or img_result is None:
        return img_result
    if len(img_result.shape) < 3 or img_result.shape[2] != 4:
        return img_result

    h_img, w_img = img_goc.shape[:2]
    if h_img < 10 or w_img < 10:
        print("   ⚠️ Ảnh quá nhỏ, bỏ qua chroma-key")
        return img_result

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

    is_dark_bg = int(dominant_bgr.mean()) < 80
    mode_str = "DARK" if is_dark_bg else "LIGHT"
    print(f"   🎨 Màu nền phát hiện (BGR): {dominant_bgr} [{mode_str}]")

    # Chỉ dùng BGR matching chặt (±7) — chỉ xoá pixel gần giống hệt màu nền
    # Không dùng HSV vì tolerance rộng bắt nhầm màu khác trong chi tiết subject
    bgr_tol = 7
    lower_bgr = np.clip(dominant_bgr.astype(int) - bgr_tol, 0, 255).astype(np.uint8)
    upper_bgr = np.clip(dominant_bgr.astype(int) + bgr_tol, 0, 255).astype(np.uint8)
    mask_bg = cv2.inRange(img_result[:, :, :3], lower_bgr, upper_bgr)
    print(f"   🔧 BGR only: tol=±{bgr_tol}")

    b_c, g_c, r_c, a_c = cv2.split(img_result)

    # Xoá mọi pixel khớp chính xác màu nền (±7 BGR), kể cả bên trong subject
    a_before = a_c.copy()
    a_c[mask_bg == 255] = 0
    print(f"   🎯 Chroma-key: {cv2.countNonZero(mask_bg)} px bị xoá")

    # Cạo viền 1px để cắt sạch răng cưa
    kernel_erode = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    a_c = cv2.erode(a_c, kernel_erode, iterations=1)

    img_result = cv2.merge([b_c, g_c, r_c, a_c])
    return img_result


def process_file(ten_file):
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"

    vao = os.path.join(THU_MUC_GOC, ten_file)
    ten_khong_duoi = ten_file.rsplit('.', 1)[0]

    tam_path = os.path.join(THU_MUC_TAM, ten_khong_duoi + '_transparent.png')
    esrgan_out = os.path.join(THU_MUC_UPSCAYLED, ten_khong_duoi + '_transparent_out.png')
    ket_qua_path = os.path.join(THU_MUC_THANH_PHAM, ten_khong_duoi + '_VIP.png')

    print(f"\n=====================================")
    print(f"🔥 ĐANG CHẶT THỊT TẤM: {ten_file}")

    try:
        # --- BƯỚC 1: TÁCH NỀN LẤY RỖNG VÀ BẮN KHỬ CHROMA KEY ---
        print("✂️ [1/3] Đang Bóc Nền Phẳng & Bắn sạch Laser nền chết...")
        img_goc = cv2.imread(vao, cv2.IMREAD_UNCHANGED)

        with open(vao, 'rb') as i:
            output_data = remove(i.read(), session=session, post_process_mask=False)

        with open(tam_path, 'wb') as o:
            o.write(output_data)

        img_result = cv2.imread(tam_path, cv2.IMREAD_UNCHANGED)

        # CHROMA-KEY: detect màu nền ngoài và xóa rác
        img_result = _detect_and_remove_chroma(img_goc, img_result)
        cv2.imwrite(tam_path, img_result)

        # --- BƯỚC 2: UPSCAYL X4 LÊN ẢNH ĐÃ RỖNG NỀN ---
        print("📈 [2/3] Quét Động cơ Upscayl Đa Dụng X4 (Tránh nhoè bẩn bề mặt)...")
        cmd = [
            UPSCAYL_ENGINE_PATH,
            '-i', tam_path,
            '-o', esrgan_out,
            '-n', 'realesrgan-x4plus',
            '-t', '0',
            '-f', 'png'
        ]
        result = subprocess.run(cmd, cwd=os.path.dirname(UPSCAYL_ENGINE_PATH), env=env)
        if result.returncode != 0:
            print(f"❌ Lỗi văng App Upscayl!")
            return

        if not os.path.exists(esrgan_out):
            print(f"⚠️ Không thấy Đầu Ra Upscayl.")
            return

        # --- BƯỚC 3: RESIZE CHUẨN X2, GẮN MÀU 300DPI ---
        print("⚒️ [3/3] Tráng Nén Ép Lại Kích Thước & Mài Bút 300DPI...")
        img_upscaled = cv2.imread(esrgan_out, cv2.IMREAD_UNCHANGED)
        if img_upscaled is not None:
            h_bua, w_bua = img_upscaled.shape[:2]
            img_upscaled = cv2.resize(img_upscaled, (w_bua // 2, h_bua // 2), interpolation=cv2.INTER_AREA)

            # Mild unsharp mask - không thay đổi màu
            blurred = cv2.GaussianBlur(img_upscaled, (0, 0), 1.0)
            img_upscaled = cv2.addWeighted(img_upscaled, 1.3, blurred, -0.3, 0)

            img_rgba = cv2.cvtColor(img_upscaled, cv2.COLOR_BGRA2RGBA)
            pil_img = Image.fromarray(img_rgba)
            pil_img.save(ket_qua_path, "PNG", dpi=(300, 300))

            # Dọn rác
            os.remove(tam_path)
            os.remove(esrgan_out)
            os.remove(vao)
            print(f"🥇 HOÀN TẤT THẦN TỐC TẤM: {ten_file}!")

    except Exception as e:
        print(f"❌ Kẹt Trục tại {ten_file} - Lỗi: {e}")

def process_single_image(input_path, output_path):
    """
    Hàm xử lý 1 ảnh: rembg trước, chroma key → upscale x4 → downscale x2, xuất 300 DPI
    """
    import tempfile, shutil

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"

    ten_file = os.path.basename(input_path)
    ten_khong_duoi = ten_file.rsplit('.', 1)[0]

    with tempfile.TemporaryDirectory() as tmpdir:
        tam_path = os.path.join(tmpdir, ten_khong_duoi + '_transparent.png')
        esrgan_out = os.path.join(tmpdir, ten_khong_duoi + '_output_out.png')

        print(f"\n=====================================")
        print(f"🔥 ĐANG CHẶT THỊT TẤM: {ten_file}")

        try:
            # --- BƯỚC 1: TÁCH NỀN VÀ MÀI CHROMA KEY ---
            print("✂️ [1/3] Đang Bóc Nền Phẳng & Bắn sạch Laser nền chết...")
            img_goc = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)

            with open(input_path, 'rb') as i:
                output_data = remove(i.read(), session=session, post_process_mask=False)

            with open(tam_path, 'wb') as o:
                o.write(output_data)

            img_result = cv2.imread(tam_path, cv2.IMREAD_UNCHANGED)

            # Khử Chroma Key
            img_result = _detect_and_remove_chroma(img_goc, img_result)
            cv2.imwrite(tam_path, img_result)

            # --- BƯỚC 2: UPSCAYL X4 LÊN ẢNH ĐÃ RỖNG NỀN ---
            print("📈 [2/3] Quét Động cơ Upscayl Đa Dụng X4...")
            cmd = [
                UPSCAYL_ENGINE_PATH,
                '-i', tam_path,
                '-o', esrgan_out,
                '-n', 'realesrgan-x4plus',
                '-t', '0',
                '-f', 'png'
            ]
            result = subprocess.run(cmd, cwd=os.path.dirname(UPSCAYL_ENGINE_PATH), env=env)
            if result.returncode != 0:
                print(f"❌ Lỗi văng App Upscayl!")
                shutil.copy2(input_path, output_path)
                return output_path

            if not os.path.exists(esrgan_out):
                print(f"⚠️ Không thấy Đầu Ra Upscayl.")
                shutil.copy2(input_path, output_path)
                return output_path

            # --- BƯỚC 3: RESIZE X2 VÀ LƯU KẾT QUẢ ---
            print("⚒️ [3/3] Tráng Nén Ép Lại Kích Thước X2 & Lưu 300DPI...")
            img_upscaled = cv2.imread(esrgan_out, cv2.IMREAD_UNCHANGED)
            if img_upscaled is not None:
                h_bua, w_bua = img_upscaled.shape[:2]
                img_upscaled = cv2.resize(img_upscaled, (w_bua // 2, h_bua // 2), interpolation=cv2.INTER_AREA)

                # Mild unsharp mask - không thay đổi màu
                blurred = cv2.GaussianBlur(img_upscaled, (0, 0), 1.0)
                img_upscaled = cv2.addWeighted(img_upscaled, 1.3, blurred, -0.3, 0)

                img_rgba = cv2.cvtColor(img_upscaled, cv2.COLOR_BGRA2RGBA)
                pil_img = Image.fromarray(img_rgba)
                pil_img.save(output_path, "PNG", dpi=(300, 300))
                print(f"🥇 HOÀN TẤT: {ten_file} → {os.path.basename(output_path)}")
                return output_path

        except Exception as e:
            print(f"❌ Kẹt Trục tại {ten_file} - Lỗi: {e}")

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
            time.sleep(5)
