import os
import cv2
import time
import subprocess
from PIL import Image
from rembg import remove, new_session
import glob
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
print("🚀 Khởi động Lưỡi Dao Cắt Nền (U2-Net - Nhổ Tận Lỗ Nách)...")
try:
    session = new_session("u2net")
except Exception as e:
    print(f"Lỗi nạp não Rembg: {e}. Vui lòng cài lại qua pip install rembg[gpu] onnxruntime-silicon")
    exit()

if not os.path.exists(UPSCAYL_ENGINE_PATH):
    print("❌ CHƯA TÌM THẤY LÕI CHẠY UPSCAYL!")
    print(f"Vui lòng kiểm tra lại đường dẫn: {UPSCAYL_ENGINE_PATH}")
    exit()

def process_file(ten_file):
    vao = os.path.join(THU_MUC_GOC, ten_file)
    ten_khong_duoi = ten_file.rsplit('.', 1)[0]
    
    tam_path = os.path.join(THU_MUC_TAM, ten_khong_duoi + '_transparent.png')
    esrgan_out = os.path.join(THU_MUC_UPSCAYLED, ten_khong_duoi + '_transparent_out.png')
    ket_qua_path = os.path.join(THU_MUC_THANH_PHAM, ten_khong_duoi + '_VIP.png')
    
    print(f"\n=====================================")
    print(f"🔥 ĐANG CHẶT THỊT TẤM: {ten_file}")
    
    try:
        # 1. BÓC NỀN
        print("✂️ [1/3] Đang bóc nền Cực Hạn (Giữ nguyên các lỗ rỗng nội bộ)...")
        with open(vao, 'rb') as i:
            # Trực tiếp tắt tính năng Lấp Lỗ Hổng của thư viện
            output_data = remove(i.read(), session=session, post_process_mask=False) 
        with open(tam_path, 'wb') as o:
            o.write(output_data)
            
        # [BỔ SUNG NÓNG] - TỰ ĐỘNG NHẬN DIỆN MÀU NỀN + CẠO VIỀN + KHOÉT MÀU NỀN TOÀN BỘ ẢNH
        print("🪒 Đang Dò Màu Nền và Bắn Laser Đục Thủng toàn bộ pixel cùng màu...")
        img_goc = cv2.imread(vao, cv2.IMREAD_UNCHANGED)
        img_tam = cv2.imread(tam_path, cv2.IMREAD_UNCHANGED)
        if img_goc is not None and img_tam is not None and img_tam.shape[2] == 4:
            # --- BƯỚC 1: TỰ ĐỘNG DETECT MÀU NỀN TỪ ẢNH GỐC (trước khi rembg cắt) ---
            h_img, w_img = img_goc.shape[:2]
            margin = max(5, min(h_img, w_img) // 20)  # Lấy 5% cạnh, tối thiểu 5px

            # Sample pixel ở 4 cạnh viền
            samples = []
            samples.extend(img_goc[0:margin, :, :3].reshape(-1, 3).tolist())        # Cạnh trên
            samples.extend(img_goc[-margin:, :, :3].reshape(-1, 3).tolist())         # Cạnh dưới
            samples.extend(img_goc[margin:-margin, 0:margin, :3].reshape(-1, 3).tolist())   # Cạnh trái
            samples.extend(img_goc[margin:-margin, -margin:, :3].reshape(-1, 3).tolist())   # Cạnh phải

            samples_arr = np.array(samples, dtype=np.uint8)

            # Tìm màu chiếm đa số bằng cách gom nhóm (quantize xuống bins thô rồi đếm)
            quantized = (samples_arr // 16) * 16 + 8  # Gom vào 16 nhóm mỗi kênh
            # Chuyển thành tuple để đếm
            from collections import Counter
            color_counts = Counter([tuple(c) for c in quantized.tolist()])
            dominant_bgr = np.array(color_counts.most_common(1)[0][0], dtype=np.uint8)

            print(f"   🎨 Màu nền phát hiện (BGR): {dominant_bgr} - Chiếm {color_counts.most_common(1)[0][1]}/{len(samples)} pixel viền")

            # --- BƯỚC 2: TẠO MASK KHOÉT DỰA TRÊN MÀU NỀN DETECT ĐƯỢC ---
            # Tolerance: cho phép sai lệch ±30 mỗi kênh BGR
            tolerance = 30
            lower_bg = np.clip(dominant_bgr.astype(int) - tolerance, 0, 255).astype(np.uint8)
            upper_bg = np.clip(dominant_bgr.astype(int) + tolerance, 0, 255).astype(np.uint8)

            bgr_only = img_tam[:, :, :3]
            mask_bg = cv2.inRange(bgr_only, lower_bg, upper_bg)

            # --- BƯỚC 3: CẠO VIỀN + ÁP MASK ---
            b_c, g_c, r_c, a_c = cv2.split(img_tam)

            # Cạo lẹm ăn sâu vào 2Px bằng lõi Toán Học 5x5
            kernel = np.ones((5, 5), np.uint8)
            a_c_eroded = cv2.erode(a_c, kernel, iterations=1)
            # Chà Nám Mìn (Smooth Blur)
            a_c_smoothed = cv2.GaussianBlur(a_c_eroded, (3, 3), 0)

            # Khoét Thủng toàn bộ pixel khớp màu nền
            a_c_smoothed[mask_bg == 255] = 0

            # Dán Trả Lại
            img_tam_clean = cv2.merge([b_c, g_c, r_c, a_c_smoothed])
            cv2.imwrite(tam_path, img_tam_clean)

        # 2. XẢ SÚNG BẰNG ĐỘNG CƠ C++ UPSCAYL ĐỘC LẬP (KHÔNG CẦN CHUỘT)
        print("📈 [2/3] Kích hoạt Động cơ Upscayl C++ Auto (Chạy Kịch Kim X4 Thuận Tự Nhiên)...")
        cmd = [
            UPSCAYL_ENGINE_PATH, 
            '-i', tam_path, 
            '-o', esrgan_out, 
            '-n', 'realesrgan-x4plus-anime', 
            '-t', '0',    # Tự động Tiling để tránh nổ Ram VRAM
            '-f', 'png'
            # (ĐÃ XÓA -s 2 VÌ LỖI MẠNG NHỆN Ô VUÔNG KHI BÓP TIẾN CHÌNH TILING)
        ]
        
        # Chạy thả rông để nó Phun thẳng Từng Phần Trăm Tính Toán (10%... 20%...) Ra màn hình 
        result = subprocess.run(cmd, cwd=os.path.dirname(UPSCAYL_ENGINE_PATH))
        if result.returncode != 0:
            print(f"Lỗi văng App Upscayl!")
            print("➡️ Lỗi này có thể do Cạc Đồ Họa Đời Cũ Của Máy Đuối Sức. Sếp chạy lại nhé.")
            return
        
        # 3. ÉP MENU MÀU LAB 300 DPI CHUẨN KDP
        if os.path.exists(esrgan_out):
            print("⚒️ [3/3] Tráng Nén Ép Lại Kích Thước & Mài Bút L.A.B Chống Đục...")
            img_upscaled = cv2.imread(esrgan_out, cv2.IMREAD_UNCHANGED)
            
            if img_upscaled is not None:
                # Ảnh hiện tại đang là X4 rát bự, mình tự động Bóp Lại bằng Thuật Toán Nén Nét Area (Siêu Phục Hồi Dữ Liệu)
                # Bóp về bằng một Nửa (Tức là X2 gốc so với ban đầu)
                h_bua, w_bua = img_upscaled.shape[:2]
                img_upscaled = cv2.resize(img_upscaled, (w_bua // 2, h_bua // 2), interpolation=cv2.INTER_AREA)

                b_up, g_up, r_up, a_up = cv2.split(img_upscaled)
                bgr_upscaled = cv2.merge([b_up, g_up, r_up])
                lab = cv2.cvtColor(bgr_upscaled, cv2.COLOR_BGR2LAB)
                str_l, str_a, str_b = cv2.split(lab)
                blurred_l = cv2.GaussianBlur(str_l, (0, 0), 4.0)
                sharpened_l = cv2.addWeighted(str_l, 2.5, blurred_l, -1.5, 0)
                merged_lab = cv2.merge([sharpened_l, str_a, str_b])
                sharpened_bgr = cv2.cvtColor(merged_lab, cv2.COLOR_LAB2BGR)
                sh_b, sh_g, sh_r = cv2.split(sharpened_bgr)
                img_final = cv2.merge([sh_b, sh_g, sh_r, a_up])

                img_in_an = cv2.cvtColor(img_final, cv2.COLOR_BGRA2RGBA)
                pil_img = Image.fromarray(img_in_an)
                
                pil_img.save(ket_qua_path, "PNG", dpi=(300, 300))
                
                # Dọn rác
                os.remove(tam_path)
                os.remove(esrgan_out)
                os.remove(vao) # Xóa file gốc luôn cho sạch
                
            print(f"🥇 HOÀN TẤT THẦN TỐC TẤM: {ten_file}!")
        else:
            print(f"⚠️ Không thấy Cục Output của Upscayl.")
            
    except Exception as e:
        print(f"❌ Kẹt Trục tại {ten_file} - Lỗi: {e}")

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
