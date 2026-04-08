import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto("https://labs.google/fx/vi/tools/flow")
        print("Final URL:", page.url)
        await browser.close()

asyncio.run(main())
