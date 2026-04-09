import os
import cv2
import time
import subprocess
from PIL import Image
from rembg import remove, new_session
import numpy as np
import tempfile
import shutil

# Upscayl engine path
_engine_name = "realesrgan-ncnn-vulkan.exe" if os.name == "nt" else "realesrgan-ncnn-vulkan"
UPSCAYL_ENGINE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", _engine_name)

# Thư mục làm việc
WORKSPACE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kdp_workspace")
THU_MUC_GOC = os.path.join(WORKSPACE_DIR, "0_HinhGoc")
THU_MUC_THANH_PHAM = os.path.join(WORKSPACE_DIR, "3_ThanhPham_VIP")

os.makedirs(THU_MUC_GOC, exist_ok=True)
os.makedirs(THU_MUC_THANH_PHAM, exist_ok=True)

# Khởi tạo rembg session
print("🚀 Khởi động rembg (ISNet)...")
try:
    session = new_session("isnet-general-use")
except Exception as e:
    raise RuntimeError(f"Lỗi nạp rembg: {e}")

if not os.path.exists(UPSCAYL_ENGINE_PATH):
    raise RuntimeError(f"Không tìm thấy Upscayl engine: {UPSCAYL_ENGINE_PATH}")

_ENV = os.environ.copy()
_ENV["PYTHONUTF8"] = "1"


# ════════════════════════════════════════════════════════════════
#  CÔNG CỤ PHỤ TRỢ
# ════════════════════════════════════════════════════════════════

def _detect_bg_color(img):
    """Chấm màu nền dominant từ 4 GÓC ảnh.
    Trả về (dominant_bgr, confidence, adaptive_tol)."""
    h, w = img.shape[:2]
    cs = max(8, min(h, w) // 15)

    tl = img[0:cs, 0:cs, :3].reshape(-1, 3)
    tr = img[0:cs, -cs:, :3].reshape(-1, 3)
    bl = img[-cs:, 0:cs, :3].reshape(-1, 3)
    br = img[-cs:, -cs:, :3].reshape(-1, 3)
    samples = np.concatenate([tl, tr, bl, br], axis=0)

    quantized = (samples // 16).astype(np.uint32)
    packed = (quantized[:, 0] << 16) | (quantized[:, 1] << 8) | quantized[:, 2]
    values, counts = np.unique(packed, return_counts=True)
    best_idx = counts.argmax()
    dominant_packed = values[best_idx]
    confidence = float(counts[best_idx]) / float(len(packed))

    b = ((dominant_packed >> 16) & 0xFF) * 16 + 8
    g = ((dominant_packed >> 8) & 0xFF) * 16 + 8
    r = (dominant_packed & 0xFF) * 16 + 8
    dominant_bgr = np.array([b, g, r], dtype=np.uint8)

    color_std = np.std(samples.astype(np.float32), axis=0).mean()
    if color_std < 10:
        adaptive_tol = 7
    elif color_std < 25:
        adaptive_tol = 12
    else:
        adaptive_tol = 18

    return dominant_bgr, confidence, adaptive_tol


def _flood_fill_remove_bg(img_bgra, bg_color, tol=7):
    """Xóa nền bằng Flood-Fill từ 4 VIỀN.
    CHỈ xóa pixel liên thông với mép — không đụng bên trong nhân vật."""
    h, w = img_bgra.shape[:2]
    bgr = img_bgra[:, :, :3]

    lower = np.clip(bg_color.astype(int) - tol, 0, 255).astype(np.uint8)
    upper = np.clip(bg_color.astype(int) + tol, 0, 255).astype(np.uint8)
    color_match = cv2.inRange(bgr, lower, upper)

    # Seed: chỉ pixel viền khớp màu
    seed = np.zeros_like(color_match)
    seed[0, :] = color_match[0, :]
    seed[-1, :] = color_match[-1, :]
    seed[:, 0] = color_match[:, 0]
    seed[:, -1] = color_match[:, -1]

    # Lan tỏa từ viền qua vùng liên thông cùng màu
    kernel = np.ones((3, 3), np.uint8)
    flood = seed.copy()
    while True:
        expanded = cv2.dilate(flood, kernel, iterations=1)
        expanded = cv2.bitwise_and(expanded, color_match)
        if np.array_equal(expanded, flood):
            break
        flood = expanded

    killed = cv2.countNonZero(flood)
    img_bgra[:, :, 3][flood == 255] = 0
    print(f"   🎯 Flood-fill: xóa {killed} px nền liên thông viền (BGR {bg_color}, ±{tol})")
    return img_bgra


def _patch_small_holes(img_bgra):
    """Vá lỗ thủng nhỏ (<2% diện tích) trong alpha mask.
    Lỗ nhỏ = rembg đánh dấu sai. Lỗ lớn = khoảng trống thật (giữ nguyên)."""
    alpha = img_bgra[:, :, 3]
    total_area = alpha.shape[0] * alpha.shape[1]
    max_hole = total_area * 0.02

    alpha_bin = (alpha > 127).astype(np.uint8) * 255
    alpha_inv = cv2.bitwise_not(alpha_bin)

    contours, hierarchy = cv2.findContours(alpha_inv, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)

    patched = 0
    if hierarchy is not None:
        for i, c in enumerate(contours):
            if hierarchy[0][i][3] != -1:  # Lỗ bên trong object
                if cv2.contourArea(c) < max_hole:
                    cv2.drawContours(alpha, [c], -1, 255, -1)
                    patched += 1

    img_bgra[:, :, 3] = alpha
    print(f"   🩹 Vá {patched} lỗ thủng nhỏ (ngưỡng < 2% diện tích)")
    return img_bgra


def _smart_erode(img_bgra):
    """Gọt viền 1px nếu không phá quá 10% pixel."""
    b_c, g_c, r_c, a_c = cv2.split(img_bgra)
    kernel_cross = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    a_eroded = cv2.erode(a_c, kernel_cross, iterations=1)

    total = cv2.countNonZero(a_c)
    if total == 0:
        return img_bgra

    lost = total - cv2.countNonZero(a_eroded)
    pct = (lost / total) * 100

    if pct > 10:
        print(f"   🛡️ Bỏ qua gọt viền ({pct:.1f}% sẽ mất — nét quá mỏng)")
        return img_bgra
    else:
        print(f"   ⛏️ Gọt viền 1px ({pct:.1f}% — an toàn)")
        return cv2.merge([b_c, g_c, r_c, a_eroded])


def _upscale_x4_to_x2(input_path):
    """Upscale ảnh GỐC CÓ NỀN (x4→x2). Không có transparency = không có rác đen."""
    input_path = os.path.abspath(input_path)
    with tempfile.NamedTemporaryFile(suffix='_x4.png', delete=False) as tmp:
        x4_path = tmp.name

    try:
        cmd = [
            UPSCAYL_ENGINE_PATH,
            '-i', input_path,
            '-o', x4_path,
            '-n', 'realesrgan-x4plus',
            '-t', '0',
            '-f', 'png'
        ]
        result = subprocess.run(cmd, cwd=os.path.dirname(UPSCAYL_ENGINE_PATH), env=_ENV,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if result.returncode != 0 or not os.path.exists(x4_path):
            return None

        img_x4 = cv2.imread(x4_path, cv2.IMREAD_UNCHANGED)
        if img_x4 is None:
            return None

        h, w = img_x4.shape[:2]
        img_x2 = cv2.resize(img_x4, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
        print(f"   📐 {w//4}x{h//4} → {w//2}x{h//2} (x4→x2)")
        return img_x2
    finally:
        if os.path.exists(x4_path):
            os.remove(x4_path)


# ════════════════════════════════════════════════════════════════
#  PIPELINE CHÍNH — CẮT TRƯỚC (NHANH), LÀM NÉT SAU (SẠCH)
# ════════════════════════════════════════════════════════════════

def _process_core(input_path):
    """Pipeline tối ưu:
    1. rembg cắt nền trên ảnh GỐC (nhỏ → nhanh, mask y hệt vì ISNet luôn resize về 1024)
    2. Upscale ảnh gốc CÓ NỀN x4→x2 → lấy RGB HD sắc nét
    3. Resize alpha mask lên HD bằng LANCZOS (giữ cạnh mượt)
    4. Ghép RGB HD + Alpha HD
    5. Vá lỗ thủng nhỏ, flood-fill chroma, smart erode
    """
    img_goc = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)
    if img_goc is None:
        return None

    # ── BƯỚC 1: CẮT NỀN TRÊN ẢNH GỐC (NHỎ, CỰC NHANH) ──
    print("✂️ [1/5] rembg cắt nền trên ảnh gốc (nhanh)...")
    with open(input_path, 'rb') as f:
        out_bytes = remove(f.read(), session=session, post_process_mask=False)

    out_arr = np.frombuffer(out_bytes, dtype=np.uint8)
    rembg_result = cv2.imdecode(out_arr, cv2.IMREAD_UNCHANGED)

    if rembg_result is None or rembg_result.shape[2] != 4:
        raise ValueError("rembg không trả về ảnh RGBA!")

    alpha_mask_small = rembg_result[:, :, 3]

    # ── BƯỚC 2: UPSCALE ẢNH GỐC CÓ NỀN → RGB HD ──
    print("📈 [2/5] Upscale x4→x2 ảnh gốc (có nền, không rác)...")
    img_hd = _upscale_x4_to_x2(input_path)
    if img_hd is None:
        print("⚠️ Upscale thất bại, dùng resize LANCZOS4")
        h, w = img_goc.shape[:2]
        img_hd = cv2.resize(img_goc, (w * 2, h * 2), interpolation=cv2.INTER_LANCZOS4)

    # ── BƯỚC 3: RESIZE ALPHA MASK LÊN KÍCH THƯỚC HD ──
    hd_h, hd_w = img_hd.shape[:2]
    alpha_mask_hd = cv2.resize(alpha_mask_small, (hd_w, hd_h), interpolation=cv2.INTER_LANCZOS4)
    print(f"   📐 Alpha mask: {alpha_mask_small.shape[1]}x{alpha_mask_small.shape[0]} → {hd_w}x{hd_h}")

    # Ghép RGB HD + Alpha HD
    b, g, r = cv2.split(img_hd[:, :, :3])
    transparent = cv2.merge([b, g, r, alpha_mask_hd])

    # ── BƯỚC 4: VÁ LỖ THỦNG NHỎ ──
    print("🩹 [3/5] Vá lỗ thủng nhỏ trong alpha mask...")
    transparent = _patch_small_holes(transparent)

    # ── BƯỚC 5: FLOOD-FILL CHROMA-KEY TỪ VIỀN ──
    print("🔫 [4/5] Flood-fill chroma-key (chỉ từ viền)...")
    bg_color, confidence, adaptive_tol = _detect_bg_color(img_hd[:, :, :3])
    print(f"   🎨 Màu nền (BGR): {bg_color}, tin cậy: {confidence:.0%}, tol: ±{adaptive_tol}")

    if confidence < 0.40:
        print("   🛡️ Bỏ qua chroma-key (tin cậy <40%)")
    else:
        transparent = _flood_fill_remove_bg(transparent, bg_color, tol=adaptive_tol)

    # ── BƯỚC 5: GỌT VIỀN THÔNG MINH ──
    transparent = _smart_erode(transparent)

    return transparent


def _save_300dpi(img_bgra, output_path):
    img_rgba = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2RGBA)
    Image.fromarray(img_rgba).save(output_path, "PNG", dpi=(300, 300))


# ════════════════════════════════════════════════════════════════
#  ENTRY POINTS
# ════════════════════════════════════════════════════════════════

def process_file(ten_file):
    vao = os.path.join(THU_MUC_GOC, ten_file)
    ten_khong_duoi = ten_file.rsplit('.', 1)[0]
    ket_qua_path = os.path.join(THU_MUC_THANH_PHAM, ten_khong_duoi + '_VIP.png')

    print(f"\n=====================================")
    print(f"🔥 XỬ LÝ: {ten_file}")

    try:
        final = _process_core(vao)
        if final is not None:
            _save_300dpi(final, ket_qua_path)
            os.remove(vao)
            print(f"🥇 HOÀN TẤT: {ten_file}")
    except Exception as e:
        print(f"❌ Lỗi tại {ten_file}: {e}")


def process_single_image(input_path, output_path):
    ten_file = os.path.basename(input_path)
    print(f"\n=====================================")
    print(f"🔥 XỬ LÝ: {ten_file}")

    try:
        final = _process_core(input_path)
        if final is not None:
            _save_300dpi(final, output_path)
            print(f"🥇 HOÀN TẤT: {ten_file} → {os.path.basename(output_path)}")
            return output_path
        else:
            print(f"❌ THẤT BẠI: {ten_file} — _process_core trả về None")
            return None
    except Exception as e:
        print(f"❌ THẤT BẠI: {ten_file} — {e}")
        return None


if __name__ == "__main__":
    print(f"🚀 BOT ĐÃ BẬT. BỎ ẢNH VÀO '{THU_MUC_GOC}'...")
    while True:
        danh_sach_file = [f for f in os.listdir(THU_MUC_GOC) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
        if danh_sach_file:
            for file in danh_sach_file:
                process_file(file)
        else:
            time.sleep(5)
