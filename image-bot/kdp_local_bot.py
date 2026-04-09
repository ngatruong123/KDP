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

# Env UTF-8 tạo sẵn 1 lần
_ENV = os.environ.copy()
_ENV["PYTHONUTF8"] = "1"

def _detect_bg_color(img):
    """Chấm màu nền dominant từ 4 GÓC ảnh (tránh nhân vật tràn viền gây nhiễu).
    Trả về (dominant_bgr, confidence_ratio, adaptive_tol).
    - confidence_ratio: tỷ lệ pixel dominant / tổng mẫu (0.0 - 1.0)
    - adaptive_tol: tolerance tự động dựa trên độ phân tán màu viền
    """
    h, w = img.shape[:2]
    corner_size = max(8, min(h, w) // 15)

    # Chỉ lấy mẫu 4 góc — vùng ít khả năng chứa nhân vật nhất
    tl = img[0:corner_size, 0:corner_size, :3].reshape(-1, 3)
    tr = img[0:corner_size, -corner_size:, :3].reshape(-1, 3)
    bl = img[-corner_size:, 0:corner_size, :3].reshape(-1, 3)
    br = img[-corner_size:, -corner_size:, :3].reshape(-1, 3)
    samples = np.concatenate([tl, tr, bl, br], axis=0)

    # Quantize và đếm dominant
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

    # Tính độ phân tán (variance) để quyết định tolerance
    # Nền solid → variance thấp → tol chặt (7)
    # Nền gradient → variance cao → tol rộng hơn (15-20)
    color_std = np.std(samples.astype(np.float32), axis=0).mean()
    if color_std < 10:
        adaptive_tol = 7    # Solid color
    elif color_std < 25:
        adaptive_tol = 12   # Nhẹ gradient
    else:
        adaptive_tol = 18   # Gradient mạnh

    return dominant_bgr, confidence, adaptive_tol

def _remove_bg_color(img_bgra, bg_color, tol=7):
    """Xóa nền dư thừa bằng Chroma Key (Chỉ quét R,G,B trùng lặp sát nút)"""
    lower = np.clip(bg_color.astype(int) - tol, 0, 255).astype(np.uint8)
    upper = np.clip(bg_color.astype(int) + tol, 0, 255).astype(np.uint8)
    mask = cv2.inRange(img_bgra[:, :, :3], lower, upper)
    img_bgra[:, :, 3][mask == 255] = 0
    print(f"   🎯 Dọn mảng màu nền tàn dư: xoá {cv2.countNonZero(mask)} px (BGR {bg_color}, ±{tol})")
    return img_bgra

def _smart_erode(img_bgra):
    """Gọt viền thông minh: chỉ erode nếu nét đủ dày. Bảo toàn lineart mỏng."""
    b_c, g_c, r_c, a_c = cv2.split(img_bgra)
    
    # Đo: nếu erode sẽ giết bao nhiêu % pixel alpha?
    kernel_cross = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    a_eroded = cv2.erode(a_c, kernel_cross, iterations=1)
    
    total_opaque = cv2.countNonZero(a_c)
    if total_opaque == 0:
        return img_bgra
    
    lost = total_opaque - cv2.countNonZero(a_eroded)
    loss_pct = (lost / total_opaque) * 100
    
    if loss_pct > 10:
        # Nét quá mỏng — erode sẽ phá hủy >10% pixel → BỎ QUA
        print(f"   🛡️ Bỏ qua gọt viền (sẽ mất {loss_pct:.1f}% pixel — nét quá mỏng)")
        return img_bgra
    else:
        print(f"   ⛏️ Gọt viền 1px (mất {loss_pct:.1f}% — an toàn)")
        return cv2.merge([b_c, g_c, r_c, a_eroded])

def _upscale_x4_to_x2_transparent(input_path, env):
    """Bơm nét x4 trên hình ĐÃ XUYÊN THẤU (Chống Ảo Giác Mực Đen)."""
    input_path = os.path.abspath(input_path)
    with tempfile.NamedTemporaryFile(suffix='_x4.png', delete=False) as tmp:
        x4_path = tmp.name

    try:
        cmd = [
            UPSCAYL_ENGINE_PATH,
            '-i', input_path,
            '-o', x4_path,
            '-n', 'realesrgan-x4plus', # BẮT BUỘC DÙNG ĐA DỤNG, KHÔNG DÙNG ANIME
            '-t', '0',
            '-f', 'png'
        ]
        result = subprocess.run(cmd, cwd=os.path.dirname(UPSCAYL_ENGINE_PATH), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if result.returncode != 0 or not os.path.exists(x4_path):
            return None

        img_x4 = cv2.imread(x4_path, cv2.IMREAD_UNCHANGED)
        if img_x4 is None: return None

        h, w = img_x4.shape[:2]
        img_x2 = cv2.resize(img_x4, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
        print(f"   📐 Nén nét siêu phẳng: {w//4}x{h//4} → {w//2}x{h//2} (x4→x2)")
        return img_x2
    finally:
        if os.path.exists(x4_path):
            os.remove(x4_path)

def _process_core_correct_order(input_path):
    """
    TRẬT TỰ ĐÚNG NHẤT:
    1. Tách rỗng nền + Xả Chroma Key trên ảnh Gốc Tốc Độ Cao.
    2. Upscale x4 bức ảnh Rỗng để chống Nhoè Viền & Tránh Ảo Giác AI.
    """
    img_goc = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)
    if img_goc is None: return None

    # BƯỚC 1: CẠO NỀN NHANH TRÊN BẢN GỐC
    print("✂️ [1/3] Bóc Tách Chóp Nền Khỏi Ảnh Gốc...")
    with open(input_path, 'rb') as f:
        out_bytes = remove(f.read(), session=session, post_process_mask=False)
    
    out_arr = np.frombuffer(out_bytes, dtype=np.uint8)
    transparent_img = cv2.imdecode(out_arr, cv2.IMREAD_UNCHANGED)
    
    if transparent_img.shape[2] != 4:
        raise ValueError("Lỗi Rembg: Hình trả về không có kênh Trong Suốt!")

    # BƯỚC 2: QUÉT CHROMA DỌN LỖ HỔNG Rỗng
    print("⚒️ [2/3] Bắn Lủng Chroma-key...")
    bg_color, confidence, adaptive_tol = _detect_bg_color(img_goc[:, :, :3])
    print(f"   🎨 Màu nền (BGR): {bg_color}, độ tin cậy: {confidence:.0%}, tol: ±{adaptive_tol}")
    
    if confidence < 0.40:
        # Mẫu màu viền quá hỗn loạn — không dám chroma-key vì sẽ bắn nhầm nhân vật
        print("   🛡️ Bỏ qua Chroma-key (độ tin cậy màu nền <40% — có thể nhân vật tràn viền)")
        clean_transparent_img = transparent_img
    else:
        clean_transparent_img = _remove_bg_color(transparent_img, bg_color, tol=adaptive_tol)
    
    # Gọt viền thông minh (tự bỏ qua nếu nét quá mỏng)
    clean_transparent_img = _smart_erode(clean_transparent_img)
    
    # Ném tạm File rỗng vô Ổ cứng để cấp cho Động Cơ Upscale
    tmp_transparent_path = os.path.join(tempfile.gettempdir(), f"clean_temp_{os.path.basename(input_path)}.png")
    cv2.imwrite(tmp_transparent_path, clean_transparent_img)

    # BƯỚC 3: BƠM NÉT CĂNG TRÊN KHUNG RỖNG
    print("📈 [3/3] Quấn AI Đôn Nét Đa Dụng (x4→x2)...")
    img_upscaled_bgra = _upscale_x4_to_x2_transparent(tmp_transparent_path, _ENV)
    
    # Dọn file mồi
    if os.path.exists(tmp_transparent_path): os.remove(tmp_transparent_path)

    # Nếu Upscale văng, trả vể bản bóc nền không phóng to
    if img_upscaled_bgra is None:
        print("⚠️ Cảnh báo: AI Phóng nổ máy, trả đỡ kết quả ảnh gốc!")
        return clean_transparent_img

    return img_upscaled_bgra

def _save_300dpi(img_bgra, output_path):
    img_rgba = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2RGBA)
    Image.fromarray(img_rgba).save(output_path, "PNG", dpi=(300, 300))

def process_file(ten_file):
    vao = os.path.join(THU_MUC_GOC, ten_file)
    ten_khong_duoi = ten_file.rsplit('.', 1)[0]
    ket_qua_path = os.path.join(THU_MUC_THANH_PHAM, ten_khong_duoi + '_VIP.png')

    print(f"\n=====================================")
    print(f"🔥 CA MỔ BÓC NỀN: {ten_file}")

    try:
        final_img = _process_core_correct_order(vao)
        if final_img is not None:
            _save_300dpi(final_img, ket_qua_path)
            os.remove(vao)
            print(f"🥇 HOÀN TẤT THỦY TINH: {ten_file}")
    except Exception as e:
        print(f"❌ Kẹt Trục tại {ten_file} - Lỗi: {e}")

def process_single_image(input_path, output_path):
    ten_file = os.path.basename(input_path)
    print(f"\n=====================================")
    print(f"🔥 CA MỔ TÀN THỂ: {ten_file}")

    try:
        final_img = _process_core_correct_order(input_path)
        if final_img is not None:
            _save_300dpi(final_img, output_path)
            print(f"🥇 HOÀN TẤT TRONG SUỐT: {ten_file} → {os.path.basename(output_path)}")
            return output_path
    except Exception as e:
        print(f"❌ Kẹt Trục tại {ten_file}: {e}")

    # Fallback
    shutil.copy2(input_path, output_path)
    return output_path

if __name__ == "__main__":
    print(f"🚀 THẦN KINH THIẾT QUÂN LUẬT ĐÃ BẬT. BỎ ẢNH VÀO '{THU_MUC_GOC}'...")
    while True:
        danh_sach_file = [f for f in os.listdir(THU_MUC_GOC) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
        if danh_sach_file:
            for file in danh_sach_file:
                process_file(file)
        else:
            time.sleep(5)
