import os
import sys
import subprocess
import argparse
import time

def main():
    parser = argparse.ArgumentParser(description="Image Bot Farm Orchestrator")
    parser.add_argument("--accounts", type=str, required=True, help="Danh sách tên các tài khoản cách nhau bằng dấu phẩy (vd: minnie,avocado,tom)")
    parser.add_argument("--headless", action="store_true", help="Chạy ẩn (không mở Window Chrome tĩnh)")
    args = parser.parse_args()

    accounts = [acc.strip() for acc in args.accounts.split(",") if acc.strip()]
    if not accounts:
        print("❌ Lỗi: Cần cung cấp ít nhất 1 tài khoản!")
        return

    # Tạo thư mục log
    os.makedirs("logs", exist_ok=True)
    
    print("========================================")
    print(f"🚀 KHỞI ĐỘNG NÔNG TRẠI BOT ({len(accounts)} Accounts)")
    print("========================================")
    
    processes = []
    
    # Kích hoạt venv (nếu chạy qua lệnh này, nó sẽ dùng python trong venv nếu có)
    python_exec = "python3"
    if os.name == "nt":
        # Windows
        if os.path.exists("venv\\Scripts\\python.exe"):
            python_exec = "venv\\Scripts\\python.exe"
        else:
            python_exec = "python"
    else:
        # Mac/Linux
        if os.path.exists("venv/bin/python"):
            python_exec = "venv/bin/python"
    
    for acc in accounts:
        log_file = open(f"logs/{acc}.log", "w", encoding="utf-8")
        
        # Thêm cờ -u (unbuffered) để ép hệ thống Python nhả ngay lập tức Text ra File, không bị trắng trơn Log.
        cmd = [python_exec, "-u", "main.py", "--acc", acc]
        if args.headless:
            cmd.append("--headless")
            
        print(f"👉 Khởi động Luồng [ {acc} ] -> Xem nhật ký tại: logs/{acc}.log")
        
        # Chạy ngầm tiến trình (Non-blocking)
        p = subprocess.Popen(cmd, stdout=log_file, stderr=log_file)
        processes.append((acc, p, log_file))
        
        # Để các acc không tranh nhau gọi Google API cùng một tíc tắc gây quá tải lúc khởi động
        time.sleep(2)
        
    print("\n✅ TẤT CẢ LUỒNG ĐÃ ĐƯỢC THẢ RA CÀY. Để tắt toàn bộ Nông trại, bấm Tổ hợp phím Ctrl + C ở cửa sổ này.")
    
    try:
        # Treo terminal gốc và liên tục rà soát xem các lính đánh thuê đã cày xong chưa
        while True:
            all_done = True
            for acc, p, log_file in processes:
                if p.poll() is None:  # Tiến trình này vẫn đang chạy
                    all_done = False
                    break
            
            if all_done:
                print("\n🎉 BÁO CÁO: TẤT CẢ CÁC LUỒNG BOT ĐÃ BÁO HẾT VIỆC VÀ TỰ ĐÓNG! Nông trại xin phép đóng cửa, chúc bạn thu hoạch vui vẻ!")
                break
                
            time.sleep(3)
            
    except KeyboardInterrupt:
        print("\n🛑 Đang Tắt Khẩn Cấp Nông Trại...")
        
    # Đảm bảo đóng dọn gàng các luồng log
    for acc, p, log_file in processes:
        if p.poll() is None:
            p.terminate()
        log_file.close()
        
    print("Nông trại đã nghỉ hưu an toàn.")
            
if __name__ == "__main__":
    main()
