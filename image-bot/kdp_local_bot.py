import os
import cv2
import time
import subprocess
from PIL import Image
from rembg import remove, new_session
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
    raise RuntimeError("Không khởi động được rembg")

if not os.path.exists(UPSCAYL_ENGINE_PATH):
    print("❌ CHƯA TÌM THẤY LÕI CHẠY UPSCAYL!")
    print(f"Vui lòng kiểm tra lại đường dẫn: {UPSCAYL_ENGINE_PATH}")
    raise RuntimeError("Không tìm thấy engine upscale")

from collections import Counter

def _detect_bg_color(img_bgr):
    """Detect màu nền dominant từ viền ảnh. Return (dominant_bgr, ratio) — ratio = tỉ lệ pixel viền khớp."""
    h_img, w_img = img_bgr.shape[:2]
    margin = max(5, min(h_img, w_img) // 20)
    samples = []
    samples.extend(img_bgr[0:margin, :, :3].reshape(-1, 3).tolist())
    samples.extend(img_bgr[-margin:, :, :3].reshape(-1, 3).tolist())
    samples.extend(img_bgr[margin:-margin, 0:margin, :3].reshape(-1, 3).tolist())
    samples.extend(img_bgr[margin:-margin, -margin:, :3].reshape(-1, 3).tolist())
    samples_arr = np.array(samples, dtype=np.uint8)
    quantized = (samples_arr // 16) * 16 + 8
    color_counts = Counter([tuple(c) for c in quantized.tolist()])
    top_color, top_count = color_counts.most_common(1)[0]
    dominant_bgr = np.array(top_color, dtype=np.uint8)
    ratio = top_count / len(samples)
    return dominant_bgr, ratio


def _chroma_key(img_bgra, dominant_bgr):
    """
    2 lớp xử lý:
    1) Toàn ảnh: ΔE < 12 → xóa (chỉ pixel gần giống 100% nền)
    2) Viền: tìm rìa alpha, clean thêm 2px quanh viền với ΔE < 30
    """
    img_lab = cv2.cvtColor(img_bgra[:, :, :3], cv2.COLOR_BGR2LAB).astype(np.float32)
    bg_lab = cv2.cvtColor(dominant_bgr.reshape(1, 1, 3), cv2.COLOR_BGR2LAB).astype(np.float32)[0][0]

    diff = img_lab - bg_lab
    delta_e = np.sqrt(np.sum(diff ** 2, axis=2))

    b_c, g_c, r_c, a_c = cv2.split(img_bgra)

    # Lớp 1: toàn ảnh — cắt pixel giống nền (ΔE < 20)
    global_mask = delta_e < 20
    a_c[global_mask] = 0
    global_count = np.count_nonzero(global_mask)

    # Lớp 2: tìm rìa alpha (biên giữa transparent và opaque)
    alpha_binary = (a_c > 0).astype(np.uint8) * 255
    kernel_edge = np.ones((5, 5), np.uint8)  # 2px quanh viền
    dilated = cv2.dilate(alpha_binary, kernel_edge, iterations=1)
    eroded = cv2.erode(alpha_binary, kernel_edge, iterations=1)
    edge_band = cv2.subtract(dilated, eroded)  # vùng 2px quanh viền

    # Trong vùng viền: cắt mạnh hơn (ΔE < 25) + fade
    edge_zone = edge_band > 0
    edge_and_bg = edge_zone & (delta_e < 30)
    fade_mask = edge_zone & (delta_e >= 20) & (delta_e < 30)
    a_float = a_c.astype(np.float32)
    a_float[edge_and_bg & (delta_e < 20)] = 0
    fade_factor = (delta_e[fade_mask] - 20) / (30 - 20)
    a_float[fade_mask] = a_float[fade_mask] * fade_factor
    a_c = np.clip(a_float, 0, 255).astype(np.uint8)

    edge_count = np.count_nonzero(edge_and_bg)
    print(f"   🔬 LAB ΔE: {global_count} pixel nền xóa + {edge_count} pixel viền clean")

    return cv2.merge([b_c, g_c, r_c, a_c])


def _remove_bg(img_sharpened, sharpened_path, dominant_bgr, bg_ratio):
    """
    Tách nền: nếu nền đơn sắc rõ (>70% viền) → chỉ dùng chroma-key (skip rembg).
    Ngược lại → rembg + chroma-key.
    """
    if bg_ratio >= 0.50:
        # Nền đơn sắc rõ ràng → chroma-key trực tiếp, KHÔNG cần rembg
        print(f"   🟢 Nền đơn sắc ({bg_ratio:.0%} viền khớp) → chỉ dùng Chroma-Key (bỏ qua rembg)")
        h, w = img_sharpened.shape[:2]
        alpha_full = np.full((h, w), 255, dtype=np.uint8)
        img_bgra = cv2.merge([img_sharpened[:, :, 0], img_sharpened[:, :, 1], img_sharpened[:, :, 2], alpha_full])
        return _chroma_key(img_bgra, dominant_bgr)
    else:
        # Nền phức tạp → dùng rembg lấy alpha + chroma-key bổ sung
        print(f"   🔵 Nền không đơn sắc ({bg_ratio:.0%}) → dùng rembg + Chroma-Key")
        with open(sharpened_path, 'rb') as i:
            output_data = remove(i.read(), session=session, post_process_mask=False)
        rembg_path = sharpened_path.rsplit('.', 1)[0] + '_rembg.png'
        with open(rembg_path, 'wb') as o:
            o.write(output_data)

        img_rembg = cv2.imdecode(np.fromfile(rembg_path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if os.path.exists(rembg_path):
            os.remove(rembg_path)

        if img_rembg is not None and img_rembg.shape[2] == 4:
            alpha_rembg = img_rembg[:, :, 3]
            img_bgra = cv2.merge([img_sharpened[:, :, 0], img_sharpened[:, :, 1], img_sharpened[:, :, 2], alpha_rembg])
        else:
            img_bgra = img_rembg

        if img_bgra is not None and img_bgra.shape[2] == 4:
            img_bgra = _chroma_key(img_bgra, dominant_bgr)
        return img_bgra


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
        result = subprocess.run(cmd, cwd=os.path.dirname(UPSCAYL_ENGINE_PATH), timeout=120)
        if result.returncode != 0:
            print(f"Lỗi văng App Upscayl!")
            print("➡️ Lỗi này có thể do Cạc Đồ Họa Đời Cũ Của Máy Đuối Sức. Sếp chạy lại nhé.")
            return

        if not os.path.exists(esrgan_out):
            print(f"⚠️ Không thấy Cục Output của Upscayl.")
            return

        # --- BƯỚC 2: TÁCH NỀN ---
        print("✂️ [2/3] Đang bóc nền...")
        img_upscaled = cv2.imdecode(np.fromfile(esrgan_out, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        img_sharpened = img_upscaled
        cv2.imwrite(tam_path, img_sharpened)

        dominant_bgr, bg_ratio = _detect_bg_color(img_sharpened)
        print(f"   🎨 Màu nền (BGR): {dominant_bgr} — chiếm {bg_ratio:.0%} viền")

        img_result = _remove_bg(img_sharpened, tam_path, dominant_bgr, bg_ratio)

        # MINIMUM (erode toàn bộ alpha 1px - co viền vào, xóa răng cưa)
        print("🪄 Đang áp Minimum toàn bộ ảnh...")
        b_f, g_f, r_f, a_f = cv2.split(img_result)
        kernel_min = np.ones((2, 2), np.uint8)
        a_f = cv2.erode(a_f, kernel_min, iterations=1)
        img_result = cv2.merge([b_f, g_f, r_f, a_f])

        # Save 300dpi
        img_rgba = cv2.cvtColor(img_result, cv2.COLOR_BGRA2RGBA)
        pil_img = Image.fromarray(img_rgba)
        pil_img.save(ket_qua_path, "PNG", dpi=(300, 300))

        # Dọn rác
        if os.path.exists(tam_path):
            os.remove(tam_path)
        os.remove(esrgan_out)
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
        result = subprocess.run(cmd, cwd=os.path.dirname(UPSCAYL_ENGINE_PATH), env=env, timeout=120)
        if result.returncode != 0:
            print(f"❌ Lỗi Upscayl!")
            return None

        if not os.path.exists(esrgan_out):
            print(f"⚠️ Không thấy output Upscayl.")
            return None

        # --- BƯỚC 2: TÁCH NỀN ---
        print("✂️ [2/3] Đang bóc nền...")
        img_upscaled = cv2.imdecode(np.fromfile(esrgan_out, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        img_sharpened = img_upscaled
        cv2.imwrite(sharpened_path, img_sharpened)

        dominant_bgr, bg_ratio = _detect_bg_color(img_sharpened)
        print(f"   🎨 Màu nền (BGR): {dominant_bgr} — chiếm {bg_ratio:.0%} viền")

        img_result = _remove_bg(img_sharpened, sharpened_path, dominant_bgr, bg_ratio)

        # MINIMUM (erode toàn bộ alpha ~0.5px - co viền vào, xóa răng cưa)
        print("🪄 Đang áp Minimum toàn bộ ảnh...")
        b_f, g_f, r_f, a_f = cv2.split(img_result)
        kernel_min = np.ones((2, 2), np.uint8)
        a_f = cv2.erode(a_f, kernel_min, iterations=1)
        img_result = cv2.merge([b_f, g_f, r_f, a_f])

        # Save 300dpi
        img_rgba = cv2.cvtColor(img_result, cv2.COLOR_BGRA2RGBA)
        pil_img = Image.fromarray(img_rgba)
        pil_img.save(output_path, "PNG", dpi=(300, 300))
        print(f"🥇 HOÀN TẤT: {ten_file} → {os.path.basename(output_path)}")
        return output_path

    # Fallback — không nên tới đây, nhưng nếu có thì báo lỗi
    print(f"❌ Lỗi không xác định khi xử lý {ten_file}")
    return None


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
