import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        print("Navigating...")
        await page.goto("https://www.yodobashi.com/product/100000001009510449/", wait_until="domcontentloaded")
        await asyncio.sleep(5)
        html = await page.content()
        import re
        jans = re.findall(r'4[59]\d{11}', html)
        print("Found JANs in HTML:", set(jans))
        print("Product Title:", await page.title())
        await browser.close()

asyncio.run(main())
