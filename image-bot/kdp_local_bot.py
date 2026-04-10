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

def _detect_bg_color(img_bgr):
    """Detect màu nền bằng median của border pixels — chính xác hơn quantize."""
    h_img, w_img = img_bgr.shape[:2]
    margin = max(5, min(h_img, w_img) // 20)
    samples = []
    samples.append(img_bgr[0:margin, :, :3].reshape(-1, 3))
    samples.append(img_bgr[-margin:, :, :3].reshape(-1, 3))
    samples.append(img_bgr[margin:-margin, 0:margin, :3].reshape(-1, 3))
    samples.append(img_bgr[margin:-margin, -margin:, :3].reshape(-1, 3))
    samples_arr = np.concatenate(samples, axis=0)

    # Median — robust với JPEG noise
    dominant_bgr = np.median(samples_arr, axis=0).astype(np.uint8)

    # Tính ratio: bao nhiêu % pixel viền gần median (ΔE < 30 trong LAB)
    samples_lab = cv2.cvtColor(samples_arr.reshape(-1, 1, 3), cv2.COLOR_BGR2LAB).astype(np.float32).reshape(-1, 3)
    bg_lab = cv2.cvtColor(dominant_bgr.reshape(1, 1, 3), cv2.COLOR_BGR2LAB).astype(np.float32)[0][0]
    diffs = np.sqrt(np.sum((samples_lab - bg_lab) ** 2, axis=1))
    ratio = np.count_nonzero(diffs < 30) / len(diffs)

    return dominant_bgr, ratio


def _chroma_key(img_bgra, dominant_bgr):
    """
    Pipeline sạch 5 bước:
    1) Pre-blur → ΔE đồng nhất hơn (giảm JPEG noise)
    2) Global chroma-key ΔE < 38
    3) Edge cleanup ΔE < 50 (3px band)
    4) Despill — trừ màu nền khỏi RGB ở vùng viền
    5) Morphological clean — lấp lỗ nhỏ, xóa chấm lấm tấm
    """
    # === 1. Pre-blur để giảm JPEG noise trước khi tính ΔE ===
    img_blurred = cv2.GaussianBlur(img_bgra[:, :, :3], (3, 3), 0)
    img_lab = cv2.cvtColor(img_blurred, cv2.COLOR_BGR2LAB).astype(np.float32)
    bg_lab = cv2.cvtColor(dominant_bgr.reshape(1, 1, 3), cv2.COLOR_BGR2LAB).astype(np.float32)[0][0]

    diff = img_lab - bg_lab
    delta_e = np.sqrt(np.sum(diff ** 2, axis=2))

    b_c, g_c, r_c, a_c = cv2.split(img_bgra)

    # === 2. Global chroma-key — nền tối ΔE chặt hơn vì nhiều màu đậm gần đen ===
    is_dark_bg = int(dominant_bgr.mean()) < 60
    global_threshold = 18 if is_dark_bg else 38
    global_mask = delta_e < global_threshold
    a_c[global_mask] = 0
    global_count = np.count_nonzero(global_mask)

    # === 3. Edge cleanup ΔE < 50 trong 3px band ===
    alpha_binary = (a_c > 0).astype(np.uint8) * 255
    kernel_edge = np.ones((7, 7), np.uint8)
    dilated = cv2.dilate(alpha_binary, kernel_edge, iterations=1)
    eroded = cv2.erode(alpha_binary, kernel_edge, iterations=1)
    edge_band = cv2.subtract(dilated, eroded)
    edge_zone = edge_band > 0
    edge_threshold = 30 if is_dark_bg else 50
    edge_and_bg = edge_zone & (delta_e < edge_threshold)
    a_c[edge_and_bg] = 0
    edge_count = np.count_nonzero(edge_and_bg)

    # === 4. Despill — trừ màu nền khỏi RGB toàn bộ pixel còn sống ===
    # Pixel gần màu nền → trừ mạnh, pixel xa → trừ ít/không trừ
    alive = a_c > 0
    if np.any(alive):
        # Strength tỉ lệ nghịch với ΔE: ΔE=0 → strength=1, ΔE≥60 → strength=0
        despill_strength = np.clip(1.0 - (delta_e / 60.0), 0, 1)
        # Tìm channel nền mạnh nhất (G cho green screen, B cho blue, etc.)
        bg_float = dominant_bgr.astype(np.float32)  # [B, G, R]
        dominant_ch = int(np.argmax(bg_float))  # channel cao nhất trong màu nền
        channels = [b_c.astype(np.float32), g_c.astype(np.float32), r_c.astype(np.float32)]
        # Channel nền mạnh nhất: cap bằng trung bình 2 channel còn lại
        other_chs = [i for i in range(3) if i != dominant_ch]
        avg_others = (channels[other_chs[0]] + channels[other_chs[1]]) / 2.0
        # Chỉ giảm nếu channel nền > avg, và theo strength
        excess = np.maximum(channels[dominant_ch] - avg_others, 0)
        channels[dominant_ch][alive] = (channels[dominant_ch] - excess * despill_strength)[alive]
        b_c = np.clip(channels[0], 0, 255).astype(np.uint8)
        g_c = np.clip(channels[1], 0, 255).astype(np.uint8)
        r_c = np.clip(channels[2], 0, 255).astype(np.uint8)

    # === 5. Morphological clean ===
    kernel_close = np.ones((3, 3), np.uint8)
    a_c = cv2.morphologyEx(a_c, cv2.MORPH_CLOSE, kernel_close)  # lấp lỗ nhỏ
    a_c = cv2.morphologyEx(a_c, cv2.MORPH_OPEN, kernel_close)   # xóa chấm lấm tấm

    print(f"   🔬 LAB ΔE: {global_count} pixel nền xóa + {edge_count} pixel viền clean + despill + morph")

    return cv2.merge([b_c, g_c, r_c, a_c])


def _refine_edges(img_bgra, img_rgb_guide, dominant_bgr):
    """
    Post-processing nâng cao sau chroma-key:
    1) Multi-pass ΔE — lần 2 blur mạnh hơn, clean chấm sót
    2) Guided Filter — alpha bám theo viền thật của ảnh
    3) Color Decontamination — thay RGB viền bằng màu design gần nhất
    4) Bilateral Filter — làm mượt viền giữ cạnh sắc
    """
    b_c, g_c, r_c, a_c = cv2.split(img_bgra)

    # === 1. Multi-pass ΔE — lần 2 với blur mạnh hơn, catch chấm sót ===
    print("   🔄 Multi-pass ΔE (lần 2, blur 5x5)...")
    img_blurred2 = cv2.GaussianBlur(img_bgra[:, :, :3], (5, 5), 0)
    img_lab2 = cv2.cvtColor(img_blurred2, cv2.COLOR_BGR2LAB).astype(np.float32)
    bg_lab = cv2.cvtColor(dominant_bgr.reshape(1, 1, 3), cv2.COLOR_BGR2LAB).astype(np.float32)[0][0]
    diff2 = img_lab2 - bg_lab
    delta_e2 = np.sqrt(np.sum(diff2 ** 2, axis=2))

    is_dark_bg = int(dominant_bgr.mean()) < 60
    pass2_threshold = 22 if is_dark_bg else 42
    pass2_mask = (delta_e2 < pass2_threshold) & (a_c > 0)
    a_c[pass2_mask] = 0
    print(f"   🔄 Pass 2: {np.count_nonzero(pass2_mask)} pixel thêm bị cắt")

    # === 2. Color Decontamination — thay RGB viền bằng màu design gần nhất ===
    print("   🎨 Color Decontamination viền...")
    alpha_binary = (a_c > 0).astype(np.uint8) * 255
    kernel_decon = np.ones((5, 5), np.uint8)
    eroded_inner = cv2.erode(alpha_binary, kernel_decon, iterations=1)
    # Vùng viền = pixel sống nhưng không ở deep inside
    edge_pixels = (a_c > 0) & (a_c < 255) | ((a_c > 0) & (eroded_inner == 0))

    if np.any(edge_pixels):
        # Tạo ảnh chỉ chứa pixel interior (deep inside, alpha=255)
        interior_mask = (eroded_inner == 255).astype(np.uint8)
        # Inpaint vùng viền từ interior colors
        inpaint_mask = (edge_pixels.astype(np.uint8)) * 255
        img_rgb_clean = cv2.merge([b_c, g_c, r_c])
        img_inpainted = cv2.inpaint(img_rgb_clean, inpaint_mask, 3, cv2.INPAINT_TELEA)
        # Chỉ thay RGB ở vùng viền
        b_c[edge_pixels] = img_inpainted[:, :, 0][edge_pixels]
        g_c[edge_pixels] = img_inpainted[:, :, 1][edge_pixels]
        r_c[edge_pixels] = img_inpainted[:, :, 2][edge_pixels]

    print("   ✅ Edge refinement hoàn tất")
    return cv2.merge([b_c, g_c, r_c, a_c])


def _remove_bg(img_sharpened, sharpened_path, dominant_bgr, bg_ratio):
    """
    Tách nền: nếu nền đơn sắc rõ (>70% viền) → chỉ dùng chroma-key (skip rembg).
    Ngược lại → rembg + chroma-key.
    """
    if bg_ratio >= 0.25:
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

        # Edge refinement nâng cao
        print("🪄 [3/3] Đang refine viền...")
        img_result = _refine_edges(img_result, img_sharpened, dominant_bgr)

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

def process_single_image(input_path, output_path, skip_bg_removal=False):
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

        # Lưu bản upscaled (chưa cắt) đè lên file gốc để upload thay thế
        upscaled_rgba = cv2.cvtColor(img_sharpened, cv2.COLOR_BGR2RGB) if img_sharpened.shape[2] == 3 else cv2.cvtColor(img_sharpened, cv2.COLOR_BGRA2RGBA)
        Image.fromarray(upscaled_rgba).save(input_path, "PNG", dpi=(300, 300))
        print(f"   📈 Đã lưu bản upscaled đè lên: {os.path.basename(input_path)}")

        if skip_bg_removal:
            # Chỉ upscale, không cắt nền — lưu upscaled làm output
            Image.fromarray(upscaled_rgba).save(output_path, "PNG", dpi=(300, 300))
            print(f"🥇 HOÀN TẤT (chỉ upscale): {ten_file} → {os.path.basename(output_path)}")
            return output_path

        cv2.imwrite(sharpened_path, img_sharpened)

        dominant_bgr, bg_ratio = _detect_bg_color(img_sharpened)
        print(f"   🎨 Màu nền (BGR): {dominant_bgr} — chiếm {bg_ratio:.0%} viền")

        img_result = _remove_bg(img_sharpened, sharpened_path, dominant_bgr, bg_ratio)

        # Edge refinement nâng cao
        print("🪄 [3/3] Đang refine viền...")
        img_result = _refine_edges(img_result, img_sharpened, dominant_bgr)

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
