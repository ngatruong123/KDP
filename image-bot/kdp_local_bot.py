import os
import cv2
import time
import subprocess
from PIL import Image
from rembg import remove, new_session
import numpy as np
import tempfile

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


def _minimum_edges(img_bgra):
    """Minimum 1px (Photoshop) trên viền NGOÀI — mượt viền rembg, không đụng bên trong."""
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    img_bgra[:, :, 3] = cv2.erode(img_bgra[:, :, 3], kernel, iterations=1)
    return img_bgra


def _remove_bg_pixels(img_bgra, lower, upper):
    """Xoá mọi pixel RGB nằm trong [lower, upper] → alpha=0.
    Áp dụng SAU Minimum để lỗ bên trong không bị erode phóng to."""
    bg_mask = cv2.inRange(img_bgra[:, :, :3], lower, upper)
    killed = cv2.countNonZero(bg_mask)
    img_bgra[:, :, 3][bg_mask == 255] = 0
    total_px = img_bgra.shape[0] * img_bgra.shape[1]
    print(f"   🎯 Xoá {killed}/{total_px} px nền (range [{lower}]-[{upper}])")
    return img_bgra



def _color_bleed_and_upscale(img_bgra):
    """Color bleed bằng cv2.inpaint: tô màu subject lan ra vùng transparent,
    rồi upscale ảnh opaque sạch → AI không bao giờ tạo rác viền đen/xám."""
    b, g, r, a = cv2.split(img_bgra)
    bgr = cv2.merge([b, g, r])

    # 1. INPAINT: Tô vùng transparent bằng màu nội suy từ subject (đúng thuật toán, 1 lần)
    inpaint_mask = (a < 128).astype(np.uint8) * 255
    bled_bgr = cv2.inpaint(bgr, inpaint_mask, inpaintRadius=15, flags=cv2.INPAINT_TELEA)

    # 2. XUẤT RA ẢNH VÀ ĐƯA CHO AI UPSCALE
    with tempfile.NamedTemporaryFile(suffix='_bled.png', delete=False) as tmp_in:
        bled_path = tmp_in.name
    with tempfile.NamedTemporaryFile(suffix='_x4.png', delete=False) as tmp_out:
        x4_path = tmp_out.name

    try:
        cv2.imwrite(bled_path, bled_bgr)
        cmd = [
            UPSCAYL_ENGINE_PATH,
            '-i', bled_path,
            '-o', x4_path,
            '-n', 'realesrgan-x4plus',
            '-t', '0',
            '-f', 'png'
        ]
        try:
            result = subprocess.run(cmd, cwd=os.path.dirname(UPSCAYL_ENGINE_PATH), env=_ENV,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
        except subprocess.TimeoutExpired:
            print(f"   ❌ Upscale timeout (>120s)")
            return None
            
        if result.returncode != 0 or not os.path.exists(x4_path):
            stderr_msg = result.stderr.decode(errors='ignore').strip()
            if stderr_msg:
                print(f"   ❌ Upscale lỗi: {stderr_msg[:200]}")
            return None

        # Đọc ảnh đã Upscale x4
        img_x4_data = np.fromfile(x4_path, dtype=np.uint8)
        if img_x4_data.size == 0:
            return None
        img_x4 = cv2.imdecode(img_x4_data, cv2.IMREAD_UNCHANGED)
        
        if img_x4 is None:
            return None

        # 3. THU HỒI VỀ x2 VÀ GHÉP TRẢ LẠI ALPHA MASK
        h, w = img_x4.shape[:2]
        img_x2_rgb = cv2.resize(img_x4, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
        
        target_w, target_h = w // 2, h // 2
        alpha_x2 = cv2.resize(a, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)
        
        b2, g2, r2 = cv2.split(img_x2_rgb)
        final = cv2.merge([b2, g2, r2, alpha_x2])
        
        print(f"   📐 {w//4}x{h//4} → {target_w}x{target_h} (x4→x2, Clear Bleed)")
        return final
        
    finally:
        for p in [bled_path, x4_path]:
            if os.path.exists(p):
                os.remove(p)


# ════════════════════════════════════════════════════════════════
#  PIPELINE CHÍNH — PHOENIX (Tái sinh từ đống tro tàn)
# ════════════════════════════════════════════════════════════════

def _detect_bg_range(img):
    """Đo chính xác range [min, max] per-channel của pixel nền từ 4 góc.
    Trả về (lower, upper, bg_color): range thật 100% của nền, không đoán tolerance."""
    h, w = img.shape[:2]
    cs = max(8, min(h, w) // 15)
    tl = img[0:cs, 0:cs, :3].reshape(-1, 3)
    tr = img[0:cs, -cs:, :3].reshape(-1, 3)
    bl = img[-cs:, 0:cs, :3].reshape(-1, 3)
    br = img[-cs:, -cs:, :3].reshape(-1, 3)
    samples = np.concatenate([tl, tr, bl, br], axis=0)

    # Lọc outlier: chỉ lấy pixel trong nhóm dominant (loại bỏ nếu góc có chi tiết design)
    quantized = (samples // 16).astype(np.uint32)
    packed = (quantized[:, 0] << 16) | (quantized[:, 1] << 8) | quantized[:, 2]
    values, counts = np.unique(packed, return_counts=True)
    dominant_packed = values[counts.argmax()]

    # Chỉ giữ pixel thuộc nhóm dominant (cùng bucket 16x16x16)
    dominant_mask = packed == dominant_packed
    dominant_samples = samples[dominant_mask]

    # Range thật per-channel: min và max thực tế của pixel nền
    lower = dominant_samples.min(axis=0).astype(np.uint8)
    upper = dominant_samples.max(axis=0).astype(np.uint8)
    bg_color = dominant_samples.mean(axis=0).astype(np.uint8)

    return lower, upper, bg_color



def _process_core(input_path):
    """Pipeline cho ảnh nền đơn sắc:
    1. Detect range màu nền thật từ 4 góc
    2. rembg → alpha mask, giữ RGB gốc
    3. Minimum 1px (mượt viền ngoài)
    4. Xoá mọi pixel RGB trong range nền (100% chính xác, không phóng to lỗ)
    5. Inpaint color bleed + AI Upscale x4→x2
    """
    img_data = np.fromfile(input_path, dtype=np.uint8)
    if img_data.size == 0:
        return None
    img_goc = cv2.imdecode(img_data, cv2.IMREAD_UNCHANGED)

    if img_goc is None:
        return None

    # ── BƯỚC 1: DETECT MÀU NỀN ──
    lower, upper, bg_color = _detect_bg_range(img_goc)
    print(f"   🎨 Màu nền (BGR): {bg_color}, range thật: [{lower}]-[{upper}]")

    # ── BƯỚC 2: CẮT NỀN (ISNET) ──
    print("✂️ [1/5] rembg (ISNet) tách nền trên ảnh gốc...")
    with open(input_path, 'rb') as f:
        out_bytes = remove(f.read(), session=session, post_process_mask=False)

    out_arr = np.frombuffer(out_bytes, dtype=np.uint8)
    transparent = cv2.imdecode(out_arr, cv2.IMREAD_UNCHANGED)

    if transparent is None or transparent.shape[2] != 4:
        raise ValueError("rembg không trả về ảnh RGBA!")

    # Lấy RGB TỪ ẢNH GỐC (rembg đôi khi tẩy màu sai)
    b_goc, g_goc, r_goc = cv2.split(img_goc[:, :, :3])
    transparent = cv2.merge([b_goc, g_goc, r_goc, transparent[:, :, 3]])

    # ── BƯỚC 3: MINIMUM 1px — mượt viền rembg ──
    print("⛏️ [2/5] Minimum 1px (mượt viền)...")
    transparent = _minimum_edges(transparent)

    # ── BƯỚC 4: XOÁ NỀN ĐƠN SẮC (sau Minimum để lỗ trong không bị phóng to) ──
    print(f"🔫 [3/5] Xoá pixel nền [{lower}]-[{upper}]...")
    transparent = _remove_bg_pixels(transparent, lower, upper)

    # ── BƯỚC 5: INPAINT COLOR BLEED + AI UPSCALE ──
    print("📈 [4/5] Color Bleed & Upscale AI x4→x2...")
    upscaled = _color_bleed_and_upscale(transparent)

    if upscaled is None:
        print("⚠️ Upscale thất bại, fallback resize LANCZOS4")
        h, w = transparent.shape[:2]
        upscaled = cv2.resize(transparent, (w*2, h*2), interpolation=cv2.INTER_LANCZOS4)

    return upscaled


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
        else:
            print(f"❌ THẤT BẠI: {ten_file} — _process_core trả về None")
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
            print(f"❌ THẤT BẠI: {ten_file} —_process_core trả về None")
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
