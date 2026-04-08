import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir="./chrome_profile", channel="chrome", headless=True)
        page = await browser.new_page()
        await page.goto("https://labs.google/fx/vi/tools/flow/project/58dcfaf6-2dd2-4b6c-96a5-32672b50678f")
        await page.wait_for_timeout(3000)
        bot_html = await page.evaluate("() => document.body.innerHTML")
        with open("dump.html", "w") as f:
            f.write(bot_html)
        await browser.close()

asyncio.run(main())
