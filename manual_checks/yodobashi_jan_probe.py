import argparse
import asyncio
import re
import sys
from urllib.parse import urlparse

from playwright.async_api import async_playwright

DEFAULT_URL = "https://www.yodobashi.com/product/100000001009510449/"
DEFAULT_WAIT_SECONDS = 5


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python3 -m manual_checks jan",
        description="Check JAN extraction on a Yodobashi product page.",
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=DEFAULT_URL,
        help="Yodobashi product URL to inspect.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Playwright in headless mode.",
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=DEFAULT_WAIT_SECONDS,
        help="Seconds to wait after page load before reading HTML.",
    )
    return parser


def validate_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "URL must start with http:// or https://"
    if "yodobashi.com" not in parsed.netloc:
        return "URL must point to yodobashi.com"
    if "/product/" not in parsed.path:
        return "URL should be a Yodobashi product page"
    return None


async def main(url=DEFAULT_URL, headless=False, wait_seconds=DEFAULT_WAIT_SECONDS):
    validation_error = validate_url(url)
    if validation_error:
        print(f"Invalid URL: {validation_error}")
        return 1

    print(f"URL: {url}")
    print(f"Headless: {'on' if headless else 'off'}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        print("Navigating...")
        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(wait_seconds)
        html = await page.content()
        jans = sorted(set(re.findall(r"4[59]\d{11}", html)))
        print("Found JANs:", jans or "None")
        print("Product Title:", await page.title())
        await browser.close()
    return 0


def run_from_cli(argv):
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(
        main(url=args.url, headless=args.headless, wait_seconds=args.wait)
    )


if __name__ == "__main__":
    raise SystemExit(run_from_cli(sys.argv[1:]))
