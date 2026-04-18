"""Mở tất cả profile Chrome, vào Google Labs Flow rồi DỪNG để check thủ công."""
import asyncio
import sys
import os
from bot import ImageBotCore

async def main():
    accounts = sys.argv[1].split(",") if len(sys.argv) > 1 else ["default"]
    bots = []

    for acc in accounts:
        acc = acc.strip()
        if not acc:
            continue
        print(f"\n{'='*40}")
        print(f"🚀 Mở profile [{acc}]...")
        bot = ImageBotCore(acc_name=acc, headless=False)
        await bot.init_browser()
        await bot.page.goto("https://labs.google/fx/vi/tools/flow", wait_until="domcontentloaded")
        await asyncio.sleep(3)
        print(f"✅ [{acc}] đã mở trang Flow. Kiểm tra popup/đăng nhập trên cửa sổ Chrome.")
        bots.append(bot)

    print(f"\n{'='*40}")
    print(f"📋 Đã mở {len(bots)} profile. Kiểm tra từng cửa sổ Chrome.")
    print("👉 Nhấn ENTER để đóng tất cả và thoát...")
    await asyncio.get_event_loop().run_in_executor(None, input)

    for bot in bots:
        await bot.close()
    print("✅ Đã đóng tất cả.")

if __name__ == "__main__":
    asyncio.run(main())
