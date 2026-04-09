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

def _patch_all_enclosed_holes(img_bgra):
    """Vá LỖ THỦNG NỘI THẤT (do AI cắt nhầm số hoặc dải băng).
    Dùng .copy() để giải quyết triệt để lỗi C-contiguous của OpenCV."""
    # .copy() tạo vùng nhớ độc lập, KHÔNG BAO GIỜ bị crash hàm drawContours nữa!
    alpha = img_bgra[:, :, 3].copy()
    total_area = alpha.shape[0] * alpha.shape[1]
    
    # Ngưỡng vá cực lớn (40% ảnh). Cứ lỗ nào nằm hoàn toàn bên trong nhân vật là VÁ CHẾT.
    max_hole = total_area * 0.40

    alpha_bin = (alpha > 127).astype(np.uint8) * 255
    alpha_inv = cv2.bitwise_not(alpha_bin)

    contours, hierarchy = cv2.findContours(alpha_inv, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)

    patched = 0
    if hierarchy is not None:
        for i, c in enumerate(contours):
            if hierarchy[0][i][3] != -1:  # -1 nghĩa là mảng nền bên ngoài, != -1 là lỗ bên trong
                if cv2.contourArea(c) < max_hole:
                    cv2.drawContours(alpha, [c], -1, 255, -1)
                    patched += 1

    img_bgra[:, :, 3] = alpha
    if patched > 0:
        print(f"   🩹 VÁ THÀNH CÔNG {patched} mảng thủng nội thất (do AI cắt nhầm).")
    return img_bgra


def _smart_erode(img_bgra):
    """Gọt viền thông minh 1px: chỉ áp dụng nếu nét không quá mỏng."""
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

def _process_core(input_path):
    """Pipeline Phẫu Thuật Đỉnh Cao:
    1. Đọc file (tránh Unicode crash)
    2. rembg cắt nền trên ảnh GỐC
    3. Patch Holes: Rịt ngay các lỗ thủng bên trong (chống lủng nội thất '250', dải băng) bằng Memory Copy an toàn.
    4. Color Bleed + Upscale: Thổi màu thịt tràn vào vùng nền rồi mới Upscale. Đuổi cổ 100% rác viền đen/xám của Google!
    5. Smart Erode: Tỉa viền sắc sảo.
    """
    img_data = np.fromfile(input_path, dtype=np.uint8)
    if img_data.size == 0:
        return None
    img_goc = cv2.imdecode(img_data, cv2.IMREAD_UNCHANGED)
    
    if img_goc is None:
        return None

    # ── BƯỚC 1: CẮT NỀN (ISNET) ──
    print("✂️ [1/4] rembg (ISNet) tách nền trên ảnh gốc...")
    with open(input_path, 'rb') as f:
        out_bytes = remove(f.read(), session=session, post_process_mask=False)

    out_arr = np.frombuffer(out_bytes, dtype=np.uint8)
    transparent = cv2.imdecode(out_arr, cv2.IMREAD_UNCHANGED)

    if transparent is None or transparent.shape[2] != 4:
        raise ValueError("rembg không trả về ảnh RGBA!")

    # BƯỚC QUAN TRỌNG: Lấy RGB TỪ ẢNH GỐC bù vào RGB của rembg (vì rembg đôi khi tẩy màu sai)
    b_goc, g_goc, r_goc = cv2.split(img_goc[:, :, :3])
    transparent = cv2.merge([b_goc, g_goc, r_goc, transparent[:, :, 3]])

    # ── BƯỚC 2: VÁ LỖ THỦNG NỘI THẤT BỊ CẮT NHẦM (SỐ, DẢI BĂNG) ──
    transparent = _patch_all_enclosed_holes(transparent)

    # ── BƯỚC 3: ĐỔ TRÀN MÀU + AI UPSCALE (DIỆT TẬN GỐC RÁC VIỀN ĐEN/XÁM) ──
    print("📈 [2/4] Color Bleed & Upscale AI x4→x2 (đuổi trừ rác viền)...")
    upscaled = _color_bleed_and_upscale(transparent)
    
    if upscaled is None:
        print("⚠️ Upscale thất bại, trả lại ảnh chưa nét")
        # Phản hồi an toàn
        h, w = transparent.shape[:2]
        upscaled = cv2.resize(transparent, (w*2, h*2), interpolation=cv2.INTER_LANCZOS4)

    # ── BƯỚC 4: GỌT VIỀN ──
    print("⛏️ [4/4] Dọn dẹp viền mỏng ngoài cùng...")
    final = _smart_erode(upscaled)

    return final


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
