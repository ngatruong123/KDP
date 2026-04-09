import os
import cv2
import time
import subprocess
from PIL import Image
from rembg import remove, new_session
import numpy as np
from collections import Counter

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


def _detect_bg_color(img):
    """Chấm màu nền dominant từ 4 viền ảnh."""
    h, w = img.shape[:2]
    margin = max(5, min(h, w) // 20)
    samples = []
    samples.extend(img[0:margin, :, :3].reshape(-1, 3).tolist())
    samples.extend(img[-margin:, :, :3].reshape(-1, 3).tolist())
    samples.extend(img[margin:-margin, 0:margin, :3].reshape(-1, 3).tolist())
    samples.extend(img[margin:-margin, -margin:, :3].reshape(-1, 3).tolist())
    samples_arr = np.array(samples, dtype=np.uint8)
    quantized = (samples_arr // 16) * 16 + 8
    color_counts = Counter([tuple(c) for c in quantized.tolist()])
    return np.array(color_counts.most_common(1)[0][0], dtype=np.uint8)


def _remove_bg_color(img_bgra, bg_color, tol=7):
    """Xoá chính xác pixel trùng màu nền (±tol) trong alpha channel."""
    lower = np.clip(bg_color.astype(int) - tol, 0, 255).astype(np.uint8)
    upper = np.clip(bg_color.astype(int) + tol, 0, 255).astype(np.uint8)
    mask = cv2.inRange(img_bgra[:, :, :3], lower, upper)
    img_bgra[:, :, 3][mask == 255] = 0
    print(f"   🎯 Chroma-key: xoá {cv2.countNonZero(mask)} px (BGR {bg_color}, ±{tol})")

    # Cạo viền 1px
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    img_bgra[:, :, 3] = cv2.erode(img_bgra[:, :, 3], kernel, iterations=1)
    return img_bgra


def _upscale_x4_to_x2(input_path, output_path, env):
    """Upscale x4 bằng realesrgan rồi resize x2. Trả về True nếu thành công."""
    import tempfile
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
        result = subprocess.run(cmd, cwd=os.path.dirname(UPSCAYL_ENGINE_PATH), env=env)
        if result.returncode != 0 or not os.path.exists(x4_path):
            print(f"❌ Upscayl thất bại")
            return False

        img_x4 = cv2.imread(x4_path, cv2.IMREAD_UNCHANGED)
        if img_x4 is None:
            return False

        h, w = img_x4.shape[:2]
        img_x2 = cv2.resize(img_x4, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
        cv2.imwrite(output_path, img_x2)
        print(f"   📐 Upscale: {w//4}x{h//4} → {w//2}x{h//2} (x4→x2)")
        return True
    finally:
        if os.path.exists(x4_path):
            os.remove(x4_path)


def _rembg_remove(input_path, output_path):
    """Cắt nền bằng rembg, lưu BGRA png."""
    with open(input_path, 'rb') as f:
        output_data = remove(f.read(), session=session, post_process_mask=False)
    with open(output_path, 'wb') as f:
        f.write(output_data)


def process_file(ten_file):
    """Xử lý file từ thư mục watcher."""
    import tempfile

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"

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

        with tempfile.TemporaryDirectory() as tmpdir:
            upscaled_path = os.path.join(tmpdir, 'upscaled.png')
            rembg_path = os.path.join(tmpdir, 'rembg.png')

            # Bước 1: Upscale x4→x2 ảnh GỐC CÓ NỀN (opaque, không vệt đen)
            print("📈 [1/3] Upscale x4→x2 ảnh gốc (có nền)...")
            if not _upscale_x4_to_x2(vao, upscaled_path, env):
                print("⚠️ Upscale fail, dùng resize thường")
                h, w = img_goc.shape[:2]
                img_x2 = cv2.resize(img_goc, (w * 2, h * 2), interpolation=cv2.INTER_LANCZOS4)
                cv2.imwrite(upscaled_path, img_x2)

            # Bước 2: rembg cắt nền trên ảnh đã upscale
            print("✂️ [2/3] rembg cắt nền...")
            _rembg_remove(upscaled_path, rembg_path)

            img_upscaled = cv2.imread(upscaled_path, cv2.IMREAD_UNCHANGED)
            img_result = cv2.imread(rembg_path, cv2.IMREAD_UNCHANGED)

            # Bước 3: Chroma-key dọn rác nền còn sót
            print("⚒️ [3/3] Chroma-key + xuất 300DPI...")
            bg_color = _detect_bg_color(img_upscaled)
            print(f"   🎨 Màu nền (BGR): {bg_color}")
            img_result = _remove_bg_color(img_result, bg_color, tol=7)

            # Xuất
            img_rgba = cv2.cvtColor(img_result, cv2.COLOR_BGRA2RGBA)
            Image.fromarray(img_rgba).save(ket_qua_path, "PNG", dpi=(300, 300))

        os.remove(vao)
        print(f"🥇 HOÀN TẤT: {ten_file}")

    except Exception as e:
        print(f"❌ Lỗi tại {ten_file}: {e}")


def process_single_image(input_path, output_path):
    """
    Xử lý 1 ảnh được gọi từ main.py:
      1. Upscale x4→x2 ảnh gốc có nền (không vệt đen)
      2. rembg cắt nền (AI chính xác)
      3. Chroma-key BGR±7 dọn rác
      4. Erode 1px + xuất 300 DPI
    """
    import tempfile, shutil

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"

    ten_file = os.path.basename(input_path)
    print(f"\n=====================================")
    print(f"🔥 XỬ LÝ: {ten_file}")

    try:
        img_goc = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)
        if img_goc is None:
            print(f"❌ Không đọc được: {input_path}")
            shutil.copy2(input_path, output_path)
            return output_path

        with tempfile.TemporaryDirectory() as tmpdir:
            upscaled_path = os.path.join(tmpdir, 'upscaled.png')
            rembg_path = os.path.join(tmpdir, 'rembg.png')

            # Bước 1: Upscale x4→x2 ảnh GỐC CÓ NỀN
            print("📈 [1/3] Upscale x4→x2 ảnh gốc (có nền)...")
            if not _upscale_x4_to_x2(input_path, upscaled_path, env):
                print("⚠️ Upscale fail, dùng resize thường")
                h, w = img_goc.shape[:2]
                img_x2 = cv2.resize(img_goc, (w * 2, h * 2), interpolation=cv2.INTER_LANCZOS4)
                cv2.imwrite(upscaled_path, img_x2)

            # Bước 2: rembg cắt nền
            print("✂️ [2/3] rembg cắt nền...")
            _rembg_remove(upscaled_path, rembg_path)

            img_upscaled = cv2.imread(upscaled_path, cv2.IMREAD_UNCHANGED)
            img_result = cv2.imread(rembg_path, cv2.IMREAD_UNCHANGED)

            # Bước 3: Chroma-key dọn rác
            print("⚒️ [3/3] Chroma-key + xuất 300DPI...")
            bg_color = _detect_bg_color(img_upscaled)
            print(f"   🎨 Màu nền (BGR): {bg_color}")
            img_result = _remove_bg_color(img_result, bg_color, tol=7)

            # Xuất
            img_rgba = cv2.cvtColor(img_result, cv2.COLOR_BGRA2RGBA)
            Image.fromarray(img_rgba).save(output_path, "PNG", dpi=(300, 300))
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
