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
    """Xóa nền bằng Flood-Fill từ 4 VIỀN ẢNH.
    CHỈ xóa pixel liên thông với mép — KHÔNG bao giờ đụng pixel bên trong nhân vật.
    Đây là điểm khác biệt quan trọng so với cv2.inRange toàn cục."""
    h, w = img_bgra.shape[:2]
    bgr = img_bgra[:, :, :3]

    # Tạo binary mask: pixel nào nằm trong dải màu nền?
    lower = np.clip(bg_color.astype(int) - tol, 0, 255).astype(np.uint8)
    upper = np.clip(bg_color.astype(int) + tol, 0, 255).astype(np.uint8)
    color_match = cv2.inRange(bgr, lower, upper)  # 255 = khớp màu nền

    # Tạo seed mask: chỉ giữ pixel khớp màu NẰM TRÊN VIỀN ẢNH
    seed = np.zeros_like(color_match)
    seed[0, :] = color_match[0, :]        # viền trên
    seed[-1, :] = color_match[-1, :]      # viền dưới
    seed[:, 0] = color_match[:, 0]        # viền trái
    seed[:, -1] = color_match[:, -1]      # viền phải

    # Flood-fill: lan tỏa từ seed qua các pixel liên thông cùng khớp màu
    # Dùng morphological reconstruction (cv2 không có sẵn, dùng vòng lặp dilate)
    kernel = np.ones((3, 3), np.uint8)
    flood = seed.copy()
    while True:
        expanded = cv2.dilate(flood, kernel, iterations=1)
        expanded = cv2.bitwise_and(expanded, color_match)  # chỉ lan vào vùng khớp màu
        if np.array_equal(expanded, flood):
            break
        flood = expanded

    # Đục alpha chỉ ở vùng flood (liên thông viền)
    killed = cv2.countNonZero(flood)
    img_bgra[:, :, 3][flood == 255] = 0
    print(f"   🎯 Flood-fill: xóa {killed} px nền liên thông viền (BGR {bg_color}, ±{tol})")
    return img_bgra


def _smart_erode(img_bgra):
    """Gọt viền thông minh: chỉ erode nếu không phá quá 10% pixel."""
    b_c, g_c, r_c, a_c = cv2.split(img_bgra)
    kernel_cross = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    a_eroded = cv2.erode(a_c, kernel_cross, iterations=1)

    total_opaque = cv2.countNonZero(a_c)
    if total_opaque == 0:
        return img_bgra

    lost = total_opaque - cv2.countNonZero(a_eroded)
    loss_pct = (lost / total_opaque) * 100

    if loss_pct > 10:
        print(f"   🛡️ Bỏ qua gọt viền (sẽ mất {loss_pct:.1f}% — nét quá mỏng)")
        return img_bgra
    else:
        print(f"   ⛏️ Gọt viền 1px (mất {loss_pct:.1f}% — an toàn)")
        return cv2.merge([b_c, g_c, r_c, a_eroded])


def _upscale_with_white_fill(img_bgra, env):
    """Upscale x4→x2 AN TOÀN cho ảnh transparent.
    
    Trick: Lót nền TRẮNG trước khi bơm vào AI → AI không sinh rác đen.
    Sau đó upscale alpha mask riêng bằng LANCZOS → ghép lại."""
    b, g, r, alpha = cv2.split(img_bgra)
    
    # Tạo bản RGB trên nền trắng (AI sẽ xử lý bản này)
    alpha_f = alpha.astype(np.float32) / 255.0
    white_bg = np.ones_like(b, dtype=np.float32) * 255.0
    r_comp = (r.astype(np.float32) * alpha_f + white_bg * (1.0 - alpha_f)).astype(np.uint8)
    g_comp = (g.astype(np.float32) * alpha_f + white_bg * (1.0 - alpha_f)).astype(np.uint8)
    b_comp = (b.astype(np.float32) * alpha_f + white_bg * (1.0 - alpha_f)).astype(np.uint8)
    img_white_bg = cv2.merge([b_comp, g_comp, r_comp])

    # Ghi tạm ra disk
    with tempfile.NamedTemporaryFile(suffix='_white.png', delete=False) as tmp_in:
        white_path = tmp_in.name
    with tempfile.NamedTemporaryFile(suffix='_x4.png', delete=False) as tmp_out:
        x4_path = tmp_out.name

    try:
        cv2.imwrite(white_path, img_white_bg)

        cmd = [
            UPSCAYL_ENGINE_PATH,
            '-i', os.path.abspath(white_path),
            '-o', os.path.abspath(x4_path),
            '-n', 'realesrgan-x4plus',
            '-t', '0',
            '-f', 'png'
        ]
        result = subprocess.run(cmd, cwd=os.path.dirname(UPSCAYL_ENGINE_PATH), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        if result.returncode != 0 or not os.path.exists(x4_path):
            return None

        img_x4_rgb = cv2.imread(x4_path, cv2.IMREAD_COLOR)
        if img_x4_rgb is None:
            return None

        # Resize RGB x4 → x2
        h4, w4 = img_x4_rgb.shape[:2]
        img_x2_rgb = cv2.resize(img_x4_rgb, (w4 // 2, h4 // 2), interpolation=cv2.INTER_AREA)

        # Resize Alpha mask riêng (không qua AI — dùng LANCZOS để giữ nét cạnh)
        h_orig, w_orig = alpha.shape[:2]
        target_w, target_h = w4 // 2, h4 // 2
        alpha_x2 = cv2.resize(alpha, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)

        # Ghép RGB đã upscale + Alpha đã resize
        b2, g2, r2 = cv2.split(img_x2_rgb)
        final = cv2.merge([b2, g2, r2, alpha_x2])

        print(f"   📐 Upscale: {w_orig}x{h_orig} → {target_w}x{target_h} (x4→x2, nền trắng an toàn)")
        return final

    finally:
        for p in [white_path, x4_path]:
            if os.path.exists(p):
                os.remove(p)


# ════════════════════════════════════════════════════════════════
#  PIPELINE CHÍNH
# ════════════════════════════════════════════════════════════════

def _process_core(input_path):
    """Pipeline hoàn chỉnh:
    1. rembg cắt nền trên ảnh gốc (nhỏ, nhanh, chính xác)
    2. Flood-fill chroma-key CHỈ từ viền (không đụng nội thất)
    3. Smart erode (tự bỏ qua nếu nét mỏng)
    4. Upscale x4→x2 trên nền trắng (chống rác đen AI)
    """
    img_goc = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)
    if img_goc is None:
        return None

    # ── BƯỚC 1: TÁCH NỀN ──
    print("✂️ [1/4] rembg tách nền...")
    with open(input_path, 'rb') as f:
        out_bytes = remove(f.read(), session=session, post_process_mask=False)

    out_arr = np.frombuffer(out_bytes, dtype=np.uint8)
    transparent = cv2.imdecode(out_arr, cv2.IMREAD_UNCHANGED)

    if transparent is None or transparent.shape[2] != 4:
        raise ValueError("rembg không trả về ảnh RGBA!")

    # ── BƯỚC 2: FLOOD-FILL CHROMA-KEY TỪ VIỀN ──
    print("🔫 [2/4] Flood-fill chroma-key (chỉ quét từ viền ảnh)...")
    bg_color, confidence, adaptive_tol = _detect_bg_color(img_goc[:, :, :3])
    print(f"   🎨 Màu nền (BGR): {bg_color}, tin cậy: {confidence:.0%}, tol: ±{adaptive_tol}")

    if confidence < 0.40:
        print("   🛡️ Bỏ qua chroma-key (tin cậy <40%)")
        clean = transparent
    else:
        clean = _flood_fill_remove_bg(transparent, bg_color, tol=adaptive_tol)

    # ── BƯỚC 3: GỌT VIỀN THÔNG MINH ──
    clean = _smart_erode(clean)

    # ── BƯỚC 4: UPSCALE TRÊN NỀN TRẮNG ──
    print("📈 [4/4] Upscale x4→x2 (lót nền trắng chống rác đen)...")
    upscaled = _upscale_with_white_fill(clean, _ENV)

    if upscaled is None:
        print("⚠️ Upscale thất bại, trả ảnh gốc đã cắt nền")
        return clean

    return upscaled


def _save_300dpi(img_bgra, output_path):
    img_rgba = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2RGBA)
    Image.fromarray(img_rgba).save(output_path, "PNG", dpi=(300, 300))


# ════════════════════════════════════════════════════════════════
#  ENTRY POINTS
# ════════════════════════════════════════════════════════════════

def process_file(ten_file):
    """Xử lý file từ thư mục watcher."""
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
    """Xử lý 1 ảnh được gọi từ main.py."""
    ten_file = os.path.basename(input_path)
    print(f"\n=====================================")
    print(f"🔥 XỬ LÝ: {ten_file}")

    try:
        final = _process_core(input_path)
        if final is not None:
            _save_300dpi(final, output_path)
            print(f"🥇 HOÀN TẤT: {ten_file} → {os.path.basename(output_path)}")
            return output_path
    except Exception as e:
        print(f"❌ Lỗi tại {ten_file}: {e}")

    shutil.copy2(input_path, output_path)
    return output_path


if __name__ == "__main__":
    print(f"🚀 BOT ĐÃ BẬT. BỎ ẢNH VÀO '{THU_MUC_GOC}'...")
    while True:
        danh_sach_file = [f for f in os.listdir(THU_MUC_GOC) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
        if danh_sach_file:
            for file in danh_sach_file:
                process_file(file)
        else:
            time.sleep(5)
