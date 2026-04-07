import os
import asyncio
import logging
import re
from urllib.parse import urlparse, parse_qs, urlunparse, urlencode
from typing import AsyncGenerator
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# Yodobashi category URL map (top-level categories researched from site)
YODOBASHI_CATEGORIES = {
    "outlet":       ("アウトレット",       "https://www.yodobashi.com/ec/category/index.html?word=%E3%82%A2%E3%82%A6%E3%83%88%E3%83%AC%E3%83%83%E3%83%88"),
    "home":         ("家電・日用品",       "https://www.yodobashi.com/category/170063/"),
    "appliances":   ("生活家電",           "https://www.yodobashi.com/category/6353/"),
    "pc":           ("パソコン・周辺機器", "https://www.yodobashi.com/category/19531/"),
    "camera":       ("カメラ・写真",       "https://www.yodobashi.com/category/19055/"),
    "audio":        ("オーディオ",         "https://www.yodobashi.com/category/22052/500000073035/"),
    "pet":          ("ペット用品・フード", "https://www.yodobashi.com/category/162842/166369/"),
    "kitchen":      ("キッチン用品・食器", "https://www.yodobashi.com/category/162842/162843/"),
    "health":       ("ヘルス＆ビューティー", "https://www.yodobashi.com/category/159888/"),
    "toys":         ("おもちゃ・ホビー",   "https://www.yodobashi.com/category/141001/141336/"),
    "food":         ("食品・飲料・お酒",   "https://www.yodobashi.com/category/157851/"),
}


class YodobashiScraper:
    def __init__(self, headless=False):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def start(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--disable-http2",
            ]
        )
        self.context = await self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="ja-JP",
            ignore_https_errors=True,
        )
        self.page = await self.context.new_page()
        await self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'languages', {
                get: () => ['ja-JP', 'ja', 'en-US', 'en']
            });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
        """)

    async def login(self):
        logger.info("YodobashiScraper: No login required for this flow.")
        return "guest"

    # ------------------------------------------------------------------
    # Scraping
    # ------------------------------------------------------------------
    async def scrape_products(
        self,
        base_url="https://www.yodobashi.com/category/19531/",
        start_page=1,
        end_page=1,
        sort_order="new_arrival",
        max_items=100,
        skip_jans=None,
    ) -> AsyncGenerator[dict, None]:

        count = 0
        skip_jans = skip_jans or []

        # Map MS-compatible sort keys → Yodobashi sorttyp values
        sort_map = {
            "new_arrival": "new_arrival",
            "price_asc": "price_asc",
            "price_desc": "price_desc",
            "score": "score",
            # MS-compatible aliases
            "disp_from_datetime": "new_arrival",
            "selling_price0_min": "price_asc",
            "selling_price0_max": "price_desc",
        }
        yodo_sort = sort_map.get(sort_order, "new_arrival")

        for page_num in range(start_page, end_page + 1):
            if count >= max_items:
                break

            # ---- Build paged URL ----
            parsed = urlparse(base_url)
            qp = parse_qs(parsed.query)
            qp["page"] = [str(page_num)]
            qp["sorttyp"] = [yodo_sort]
            target_url = urlunparse((
                parsed.scheme, parsed.netloc, parsed.path,
                parsed.params, urlencode(qp, doseq=True), parsed.fragment,
            ))

            logger.info(f"[Yodobashi] Page {page_num}: {target_url}")
            yield {"type": "log", "msg": f"📄 ヨドバシ ページ {page_num} を読み込み中..."}

            try:
                await self.page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(4)
            except Exception as e:
                logger.error(f"Page load error: {e}")
                yield {"type": "log", "msg": f"⚠️ ページ {page_num} の読み込みに失敗。リトライ中..."}
                # Retry once with a longer wait
                try:
                    await asyncio.sleep(3)
                    await self.page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                    await asyncio.sleep(5)
                except Exception:
                    yield {"type": "log", "msg": f"❌ ページ {page_num} を読み込めません。スキップします。"}
                    continue

            # ---- Collect product links ----
            link_els = await self.page.locator('a[href*="/product/"]').all()
            seen, product_urls = set(), []
            for el in link_els:
                href = await el.get_attribute("href")
                if not href or "/product/" not in href:
                    continue
                href = href.split("?")[0]
                if "review" in href or "question" in href:
                    continue
                if href in seen:
                    continue
                seen.add(href)
                full = href if href.startswith("http") else f"https://www.yodobashi.com{href}"
                product_urls.append(full)

            logger.info(f"[Yodobashi] Found {len(product_urls)} products on page {page_num}")

            if not product_urls:
                yield {"type": "log", "msg": "⚠️ 商品が見つかりませんでした。"}
                continue  # try next page instead of breaking

            # ---- Scrape each product ----
            for item_url in product_urls:
                if count >= max_items:
                    break

                count += 1
                logger.info(f"[Yodobashi] Scraping [{count}/{max_items}]: {item_url}")
                yield {"type": "log", "msg": f"🔍 商品を分析中... ({count}/{max_items})"}

                try:
                    await self.page.goto(item_url, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"Product page load error: {e}")
                    continue

                # --- Extract data ---
                title = await self._extract_title()
                price = await self._extract_price()
                brand = await self._extract_brand()
                jan_code = await self._extract_jan()

                if jan_code and jan_code in skip_jans:
                    continue

                yield {
                    "type": "item",
                    "data": {
                        "id": jan_code or f"yodo_{count}",
                        "jan": jan_code,  # May be empty string ""
                        "title": title,
                        "brand": brand,
                        "price": price,
                        "ms_url": item_url,
                        "page": page_num,
                        "index": count,
                        "points_rate": 0.10,  # Yodobashi standard 10%
                    },
                }

    # ------------------------------------------------------------------
    # Data extraction helpers
    # ------------------------------------------------------------------
    async def _extract_title(self) -> str:
        try:
            # Try multiple selectors in priority order
            for sel in ["h1.productName", "h1"]:
                el = self.page.locator(sel).first
                if await el.count() > 0:
                    txt = await el.text_content()
                    if txt and txt.strip():
                        return txt.strip()
        except Exception:
            pass
        return "不明"

    async def _extract_price(self) -> int:
        try:
            for sel in [".productPrice .pPrice", "#js_scl_priceWhole", ".productPrice"]:
                el = self.page.locator(sel).first
                if await el.count() > 0:
                    txt = await el.text_content()
                    digits = re.sub(r"[^\d]", "", txt)
                    if digits:
                        return int(digits)
        except Exception:
            pass
        return 0

    async def _extract_brand(self) -> str:
        try:
            el = self.page.locator('a[href*="/manufacturer/"], a[href*="/maker/"]').first
            if await el.count() > 0:
                txt = await el.text_content()
                if txt and txt.strip():
                    return txt.strip()
        except Exception:
            pass
        return "不明"

    async def _extract_jan(self) -> str:
        """Try to find a 13-digit JAN code starting with 45 or 49."""
        try:
            # 1) Look in the spec table area
            spec_area = self.page.locator("#prdSpec, .pSpec, #spec, table")
            for i in range(await spec_area.count()):
                txt = await spec_area.nth(i).text_content()
                if txt:
                    m = re.search(r"(4[59]\d{11})", txt)
                    if m:
                        return m.group(1)
            # 2) Look in full page body as a last resort
            body = await self.page.locator("body").text_content()
            if body:
                m = re.search(r"(4[59]\d{11})", body)
                if m:
                    return m.group(1)
        except Exception:
            pass
        return ""

    async def stop(self):
        try:
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass
