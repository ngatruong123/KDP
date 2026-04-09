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


def _upscale_x4_to_x2(input_path):
    """Upscale ảnh GỐC CÓ NỀN (x4→x2). Giúp AI không bị ảo giác rác đen ở vùng trong suốt."""
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

        # cv2.imread có thể lỗi silent trên Windows nếu path có ký tự lạ
        img_x4_data = np.fromfile(x4_path, dtype=np.uint8)
        if img_x4_data.size == 0:
            return None
        img_x4 = cv2.imdecode(img_x4_data, cv2.IMREAD_UNCHANGED)
        
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
#  PIPELINE CHÍNH — TỐI GIẢN TỐI ĐA (CHỈ GIAO VIỆC CHO AI)
# ════════════════════════════════════════════════════════════════

def _process_core(input_path):
    """Pipeline tối giản & an toàn tuyệt đối:
    1. rembg (AI) cắt nền trên ảnh GỐC (nhỏ → nhanh)
    2. Upscale (AI) ảnh gốc CÓ NỀN x4→x2 → lấy RGB HD sắc nét
    3. Resize alpha mask lên HD bằng LANCZOS (giữ cạnh mượt)
    4. Ghép RGB HD + Alpha HD
    5. Smart erode (gọt viền thừa)
    
    Lý do hủy bỏ color-based (chroma key / flood fill):
    Chroma-key phá nát các vùng màu sáng (kim cương trắng, chữ bạc) vì màu của chúng trùng với màu nền sáng, đặc biệt khi các dải màu này nằm sát viền ảnh dẫn đến thuật toán đổ len lỏi vào nội thất ảnh. Rút kinh nghiệm, ta giao toàn bộ quyền sinh sát Alpha mask cho mạng neural (ISNet) để tránh cắt mù quáng.
    """
    # cv2.imread bị lỗi silent (trả về None) trên Windows nếu đường dẫn có dấu tiếng Việt
    # Dùng np.fromfile + cv2.imdecode để tránh hoàn toàn lỗi này
    img_data = np.fromfile(input_path, dtype=np.uint8)
    if img_data.size == 0:
        return None
    img_goc = cv2.imdecode(img_data, cv2.IMREAD_UNCHANGED)
    
    if img_goc is None:
        return None

    # ── BƯỚC 1: CẮT NỀN BẰNG REMBG (TRÊN ẢNH NHỎ) ──
    print("✂️ [1/3] rembg (ISNet) tách nền trên ảnh gốc...")
    with open(input_path, 'rb') as f:
        out_bytes = remove(f.read(), session=session, post_process_mask=False)

    out_arr = np.frombuffer(out_bytes, dtype=np.uint8)
    rembg_result = cv2.imdecode(out_arr, cv2.IMREAD_UNCHANGED)

    if rembg_result is None or rembg_result.shape[2] != 4:
        raise ValueError("rembg không trả về ảnh RGBA!")

    alpha_mask_small = rembg_result[:, :, 3]

    # ── BƯỚC 2: UPSCALE ẢNH GỐC CÓ NỀN → RGB HD ──
    print("📈 [2/3] Upscale AI x4→x2 ảnh gốc (giữ nguyên nền chống ám đen)...")
    img_hd = _upscale_x4_to_x2(input_path)
    if img_hd is None:
        print("⚠️ Upscale thất bại, fallback sang resize LANCZOS4")
        h, w = img_goc.shape[:2]
        img_hd = cv2.resize(img_goc, (w * 2, h * 2), interpolation=cv2.INTER_LANCZOS4)

    # ── BƯỚC 3: RESIZE ALPHA MASK LÊN HD VÀ GHÉP ──
    hd_h, hd_w = img_hd.shape[:2]
    alpha_mask_hd = cv2.resize(alpha_mask_small, (hd_w, hd_h), interpolation=cv2.INTER_LANCZOS4)
    print(f"   📐 Resize Alpha Mask lên độ phân giải HD")

    # Ghép RGB HD (sắc nét, không rác) + Alpha HD (chính xác)
    b, g, r = cv2.split(img_hd[:, :, :3])
    transparent = cv2.merge([b, g, r, alpha_mask_hd])

    # ── BƯỚC 4: GỌT VIỀN ──
    print("⛏️ [3/3] Dọn dẹp viền mỏng ngoài cùng...")
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
