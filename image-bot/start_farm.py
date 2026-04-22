import os
import sys
import subprocess
import argparse
import time

MAX_CONSECUTIVE_ERRORS = 3
# Keyword báo hiệu 1 job thất bại (bot không tạo/download được ảnh)
ERROR_KEYWORDS = [
    "Lỗi Web Vĩnh Viễn", "LỖI CHÍ MẠNG", "Lỗi khởi tạo API",
    "Không thấy nhô ra ảnh mới nào",   # Bot tạo ảnh nhưng không thấy output
    "LỖI TẢI ẢNH GỐC",                # Upload ảnh gốc thất bại
    "LỖI GIAO DIỆN CHAT",              # Không thao tác được UI
    "Lỗi Web ❌",                       # Job lỗi web (kể cả không vĩnh viễn)
    "Lỗi văng App ❌",                  # Bot crash giữa chừng
    "❌❌❌ LỖI BOT",                    # Exception không bắt được
    "TIMEOUT",
]
# Keyword báo hiệu 1 job thành công (có ảnh download được)
SUCCESS_KEYWORDS = ["HOÀN TẤT", "📥 Đã hứng thành công ảnh"]

def count_recent_errors(log_path, last_pos):
    """Đọc log từ vị trí cuối cùng, đếm lỗi liên tiếp gần nhất.
    Chỉ reset về 0 khi có JOB THÀNH CÔNG (có ảnh download), không reset bởi emoji ✅ lung tung."""
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            f.seek(last_pos)
            new_content = f.read()
            new_pos = f.tell()

        if not new_content:
            return 0, new_pos

        errors = 0
        for line in new_content.strip().split("\n"):
            if any(kw in line for kw in ERROR_KEYWORDS):
                errors += 1
            elif any(kw in line for kw in SUCCESS_KEYWORDS):
                errors = 0  # Chỉ reset khi thực sự có ảnh được tạo/download

        return errors, new_pos
    except Exception:
        return 0, last_pos

def spawn_bot(acc, python_exec, headless, no_cut, resume_from=None, proxy=None, fingerprint=False, cdp_port=None):
    """Khởi động 1 bot, trả về (process, log_file). resume_from = tên acc cũ bị fail để bot mới nhặt lại dòng kẹt."""
    log_file = open(f"logs/{acc}.log", "w", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    cmd = [python_exec, "-u", "main.py", "--acc", acc]
    if headless:
        cmd.append("--headless")
    if no_cut:
        cmd.append("--no-cut")
    if resume_from:
        cmd.extend(["--resume-from", resume_from])
    if proxy:
        cmd.extend(["--proxy", proxy])
    if fingerprint:
        cmd.append("--fingerprint")
        if cdp_port:
            cmd.extend(["--cdp-port", str(cdp_port)])

    p = subprocess.Popen(cmd, stdout=log_file, stderr=log_file, env=env)
    return p, log_file

def main():
    parser = argparse.ArgumentParser(description="Image Bot Farm Orchestrator")
    parser.add_argument("--accounts", type=str, required=True, help="Danh sách tên các tài khoản cách nhau bằng dấu phẩy (vd: minnie,avocado,tom)")
    parser.add_argument("--backup-accounts", type=str, default="", help="Danh sách acc dự bị (vd: backup1,backup2)")
    parser.add_argument("--headless", action="store_true", help="Chạy ẩn (không mở Window Chrome tĩnh)")
    parser.add_argument("--no-cut", action="store_true", help="Chỉ upscale, không cắt nền")
    parser.add_argument("--proxy", type=str, default="", help="Proxy server (vd: http://user:pass@ip:port) — tất cả bot sẽ chạy qua proxy này")
    parser.add_argument("--fingerprint", action="store_true", help="Dùng fingerprint-chromium thay vì Chrome mặc định")
    args = parser.parse_args()

    accounts = [acc.strip() for acc in args.accounts.split(",") if acc.strip()]
    backup_accounts = [acc.strip() for acc in args.backup_accounts.split(",") if acc.strip()]

    if not accounts:
        print("❌ Lỗi: Cần cung cấp ít nhất 1 tài khoản!")
        return

    # Tạo thư mục log
    os.makedirs("logs", exist_ok=True)

    # Tìm python exec
    python_exec = "python3"
    if os.name == "nt":
        if os.path.exists("venv\\Scripts\\python.exe"):
            python_exec = "venv\\Scripts\\python.exe"
        else:
            python_exec = "python"
    else:
        if os.path.exists("venv/bin/python"):
            python_exec = "venv/bin/python"

    proxy = args.proxy.strip() if args.proxy else ""

    print("========================================")
    print(f"🚀 KHỞI ĐỘNG NÔNG TRẠI BOT ({len(accounts)} Accounts, {len(backup_accounts)} Dự bị)")
    if proxy:
        print(f"🌐 Proxy: {proxy}")
    print("========================================")

    # slots: mỗi slot = {acc, process, log_file, error_count, log_pos}
    slots = []

    for i, acc in enumerate(accounts):
        cdp_port = 9222 + i if args.fingerprint else None
        p, log_file = spawn_bot(acc, python_exec, args.headless, args.no_cut, proxy=proxy, fingerprint=args.fingerprint, cdp_port=cdp_port)
        slots.append({
            "acc": acc,
            "process": p,
            "log_file": log_file,
            "error_count": 0,
            "log_pos": 0,
            "active": True,
            "cdp_port": cdp_port,
        })
        print(f"👉 Khởi động Luồng [ {acc} ] -> logs/{acc}.log" + (f" (CDP:{cdp_port})" if cdp_port else ""))
        time.sleep(10)  # Giãn 10s giữa các bot để tránh peak CPU/RAM khi khởi động đồng thời

    print(f"\n✅ TẤT CẢ LUỒNG ĐÃ ĐƯỢC THẢ RA CÀY. Ctrl + C để tắt.")
    if backup_accounts:
        print(f"🔄 Acc dự bị: {', '.join(backup_accounts)}")

    replaced_count = 0

    try:
        while True:
            all_done = True

            for slot in slots:
                if not slot["active"]:
                    continue

                # Bot đã tự thoát (crash hoặc hết việc)
                if slot["process"].poll() is not None:
                    exit_code = slot["process"].returncode
                    # Exit code 0 = hết việc bình thường, khác 0 = crash
                    if exit_code != 0:
                        old_acc = slot["acc"]
                        print(f"\n💀 Bot [{old_acc}] crash (exit code {exit_code})!")
                        slot["log_file"].close()
                        if backup_accounts:
                            new_acc = backup_accounts.pop(0)
                            print(f"🔄 Thay thế [{old_acc}] -> [{new_acc}]")
                            time.sleep(3)
                            p, log_file = spawn_bot(new_acc, python_exec, args.headless, args.no_cut, resume_from=old_acc, proxy=proxy, fingerprint=args.fingerprint, cdp_port=slot.get("cdp_port"))
                            slot["acc"] = new_acc
                            slot["process"] = p
                            slot["log_file"] = log_file
                            slot["error_count"] = 0
                            slot["log_pos"] = 0
                            replaced_count += 1
                            print(f"👉 Bot [{new_acc}] đã lên sàn! -> logs/{new_acc}.log")
                            continue
                        else:
                            print(f"❌ Hết acc dự bị! Bot [{old_acc}] dừng vĩnh viễn.")
                            slot["active"] = False
                    continue

                all_done = False

                # Đếm lỗi mới trong log
                log_path = f"logs/{slot['acc']}.log"
                new_errors, new_pos = count_recent_errors(log_path, slot["log_pos"])
                slot["log_pos"] = new_pos
                slot["error_count"] += new_errors

                # Quá nhiều lỗi liên tiếp → thay thế
                if slot["error_count"] >= MAX_CONSECUTIVE_ERRORS:
                    old_acc = slot["acc"]
                    print(f"\n⚠️ Bot [{old_acc}] fail {slot['error_count']} lần liên tiếp!")

                    # Kill bot cũ
                    slot["process"].terminate()
                    slot["log_file"].close()

                    if backup_accounts:
                        new_acc = backup_accounts.pop(0)
                        print(f"🔄 Thay thế [{old_acc}] -> [{new_acc}]")

                        time.sleep(3)
                        p, log_file = spawn_bot(new_acc, python_exec, args.headless, args.no_cut, resume_from=old_acc, proxy=proxy, fingerprint=args.fingerprint, cdp_port=slot.get("cdp_port"))
                        slot["acc"] = new_acc
                        slot["process"] = p
                        slot["log_file"] = log_file
                        slot["error_count"] = 0
                        slot["log_pos"] = 0
                        replaced_count += 1
                        print(f"👉 Bot [{new_acc}] đã lên sàn! -> logs/{new_acc}.log")
                    else:
                        print(f"❌ Hết acc dự bị! Bot [{old_acc}] dừng vĩnh viễn.")
                        slot["active"] = False

            # Check tất cả đã xong chưa
            active_running = [s for s in slots if s["active"] and s["process"].poll() is None]
            if len(active_running) == 0:
                print(f"\n🎉 TẤT CẢ BOT ĐÃ XONG! (Đã thay thế {replaced_count} lần)")
                break

            time.sleep(10)

    except KeyboardInterrupt:
        print("\n🛑 Đang Tắt Khẩn Cấp Nông Trại...")

    # Dọn dẹp
    for slot in slots:
        if slot["process"].poll() is None:
            slot["process"].terminate()
        slot["log_file"].close()

    print("Nông trại đã nghỉ hưu an toàn.")

if __name__ == "__main__":
    main()
