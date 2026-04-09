import os
import cv2
import time
import subprocess
from PIL import Image
from rembg import remove, new_session
import numpy as np

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

# Env UTF-8 tạo sẵn 1 lần
_ENV = os.environ.copy()
_ENV["PYTHONUTF8"] = "1"


def _detect_bg_color(img):
    """Chấm màu nền dominant từ 4 viền ảnh. Dùng numpy thuần, không .tolist()."""
    h, w = img.shape[:2]
    margin = max(5, min(h, w) // 20)
    # Gom 4 viền thành 1 mảng numpy liên tục
    top = img[0:margin, :, :3].reshape(-1, 3)
    bot = img[-margin:, :, :3].reshape(-1, 3)
    left = img[margin:-margin, 0:margin, :3].reshape(-1, 3)
    right = img[margin:-margin, -margin:, :3].reshape(-1, 3)
    samples = np.concatenate([top, bot, left, right], axis=0)

    # Quantize và tìm dominant bằng numpy — nhanh hơn Counter 10-50x
    quantized = (samples // 16).astype(np.uint32)
    # Pack 3 kênh thành 1 số duy nhất để đếm
    packed = (quantized[:, 0] << 16) | (quantized[:, 1] << 8) | quantized[:, 2]
    values, counts = np.unique(packed, return_counts=True)
    dominant_packed = values[counts.argmax()]
    # Unpack
    b = ((dominant_packed >> 16) & 0xFF) * 16 + 8
    g = ((dominant_packed >> 8) & 0xFF) * 16 + 8
    r = (dominant_packed & 0xFF) * 16 + 8
    return np.array([b, g, r], dtype=np.uint8)


def _remove_bg_color(img_bgra, bg_color, tol=7):
    """Xoá TOÀN BỘ pixel trùng chính xác màu nền (±tol) — kể cả bên trong object.
    Không erode để tránh gặm lỗ."""
    lower = np.clip(bg_color.astype(int) - tol, 0, 255).astype(np.uint8)
    upper = np.clip(bg_color.astype(int) + tol, 0, 255).astype(np.uint8)
    mask = cv2.inRange(img_bgra[:, :, :3], lower, upper)
    img_bgra[:, :, 3][mask == 255] = 0
    print(f"   🎯 Chroma-key: xoá {cv2.countNonZero(mask)} px (BGR {bg_color}, ±{tol})")
    return img_bgra


def _upscale_x4_to_x2(input_path, env):
    """Upscale x4 bằng realesrgan rồi resize x2. Trả về numpy array hoặc None."""
    import tempfile
    # Chuyển sang absolute path — vì cwd sẽ đổi sang bin/ khi gọi engine
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
        result = subprocess.run(cmd, cwd=os.path.dirname(UPSCAYL_ENGINE_PATH), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if result.returncode != 0 or not os.path.exists(x4_path):
            stderr_msg = result.stderr.decode(errors='ignore').strip()
            if stderr_msg:
                print(f"   ❌ Upscale engine lỗi: {stderr_msg[:200]}")
            return None

        img_x4 = cv2.imread(x4_path, cv2.IMREAD_UNCHANGED)
        if img_x4 is None:
            return None

        h, w = img_x4.shape[:2]
        img_x2 = cv2.resize(img_x4, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
        print(f"   📐 Upscale: {w//4}x{h//4} → {w//2}x{h//2} (x4→x2)")
        return img_x2
    finally:
        if os.path.exists(x4_path):
            os.remove(x4_path)


def _rembg_in_memory(img_bgr):
    """Cắt nền bằng rembg in-memory.
    CHỈ lấy alpha mask từ rembg, giữ nguyên RGB gốc — tránh rembg sửa màu/tạo vệt đen."""
    _, buf = cv2.imencode('.png', img_bgr)
    out_bytes = remove(buf.tobytes(), session=session, post_process_mask=False)
    out_arr = np.frombuffer(out_bytes, dtype=np.uint8)
    rembg_out = cv2.imdecode(out_arr, cv2.IMREAD_UNCHANGED)

    # Lấy alpha từ rembg, ghép với RGB gốc
    alpha = rembg_out[:, :, 3] if rembg_out.shape[2] == 4 else np.full(img_bgr.shape[:2], 255, dtype=np.uint8)
    b, g, r = cv2.split(img_bgr[:, :, :3])
    return cv2.merge([b, g, r, alpha])


def _process_core(img_goc, input_path_for_upscale):
    """Logic xử lý chung: upscale → rembg → chroma → trả về BGRA."""
    # Bước 1: Upscale x4→x2 ảnh GỐC CÓ NỀN
    print("📈 [1/3] Upscale x4→x2 ảnh gốc (có nền)...")
    img_upscaled = _upscale_x4_to_x2(input_path_for_upscale, _ENV)
    if img_upscaled is None:
        print("⚠️ Upscale fail, dùng resize LANCZOS4")
        h, w = img_goc.shape[:2]
        img_upscaled = cv2.resize(img_goc, (w * 2, h * 2), interpolation=cv2.INTER_LANCZOS4)

    # Bước 2: rembg cắt nền in-memory (không ghi disk)
    print("✂️ [2/3] rembg cắt nền...")
    img_result = _rembg_in_memory(img_upscaled)

    # Bước 3: Chroma-key
    print("⚒️ [3/3] Chroma-key...")
    bg_color = _detect_bg_color(img_upscaled)
    print(f"   🎨 Màu nền (BGR): {bg_color}")
    img_result = _remove_bg_color(img_result, bg_color, tol=7)

    return img_result


def _save_300dpi(img_bgra, output_path):
    """Chuyển BGRA → RGBA và lưu PNG 300 DPI."""
    img_rgba = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2RGBA)
    Image.fromarray(img_rgba).save(output_path, "PNG", dpi=(300, 300))


def process_file(ten_file):
    """Xử lý file từ thư mục watcher."""
    vao = os.path.join(THU_MUC_GOC, ten_file)
    ten_khong_duoi = ten_file.rsplit('.', 1)[0]
    ket_qua_path = os.path.join(THU_MUC_THANH_PHAM, ten_khong_duoi + '_VIP.png')

    print(f"\n=====================================")
    print(f"🔥 XỬ LÝ: {ten_file}")

    try:
        img_goc = cv2.imread(vao, cv2.IMREAD_UNCHANGED)
        if img_goc is None:
            print(f"❌ Không đọc được: {vao}")
            return

        img_result = _process_core(img_goc, vao)
        _save_300dpi(img_result, ket_qua_path)

        os.remove(vao)
        print(f"🥇 HOÀN TẤT: {ten_file}")

    except Exception as e:
        print(f"❌ Lỗi tại {ten_file}: {e}")


def process_single_image(input_path, output_path):
    """
    Xử lý 1 ảnh được gọi từ main.py:
      1. Upscale x4→x2 ảnh gốc có nền (AI làm nét)
      2. rembg cắt nền in-memory (chỉ lấy alpha)
      3. Chroma-key BGR±7 xoá chính xác màu nền
      4. Xuất PNG 300 DPI
    """
    import shutil

    ten_file = os.path.basename(input_path)
    print(f"\n=====================================")
    print(f"🔥 XỬ LÝ: {ten_file}")

    try:
        img_goc = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)
        if img_goc is None:
            print(f"❌ Không đọc được: {input_path}")
            shutil.copy2(input_path, output_path)
            return output_path

        img_result = _process_core(img_goc, input_path)
        _save_300dpi(img_result, output_path)
        print(f"🥇 HOÀN TẤT: {ten_file} → {os.path.basename(output_path)}")
        return output_path

    except Exception as e:
        print(f"❌ Lỗi tại {ten_file}: {e}")

    shutil.copy2(input_path, output_path)
    return output_path


if __name__ == "__main__":
    print(f"🚀 BOT ĐÃ BẬT. BỎ ẢNH VÀO '{THU_MUC_GOC}'.")
    while True:
        danh_sach_file = [f for f in os.listdir(THU_MUC_GOC) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
        if danh_sach_file:
            for file in danh_sach_file:
                process_file(file)
        else:
            time.sleep(5)
