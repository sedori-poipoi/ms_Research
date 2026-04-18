import os
import asyncio
import logging
import re
from urllib.parse import urlparse, parse_qs, urlunparse, urlencode
from typing import AsyncGenerator
from playwright.async_api import async_playwright
from core.site_config import YODOBASHI_CATEGORIES

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class YodobashiScraper:
    def __init__(self, headless=False):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def _safe_wait(self, min_sec=1, max_sec=3):
        import random
        await asyncio.sleep(random.uniform(min_sec, max_sec))

    async def start(self):
        self.playwright = await async_playwright().start()
        
        launch_args = [
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled"
        ]
        if self.headless:
            launch_args.append("--disable-gpu")

        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            channel="chrome",
            args=launch_args
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

    def _build_listing_url(self, base_url, page_num, sort_order):
        sort_map = {
            "new_arrival": "new_arrival",
            "price_asc": "price_asc",
            "price_desc": "price_desc",
            "score": "score",
            "disp_from_datetime": "new_arrival",
            "selling_price0_min": "price_asc",
            "selling_price0_max": "price_desc",
        }
        yodo_sort = sort_map.get(sort_order, "new_arrival")

        parsed = urlparse(base_url)
        qp = parse_qs(parsed.query)
        path_parts = [p for p in parsed.path.split('/') if p]
        numeric_ids = [p for p in path_parts if p.isdigit()]

        if "category" in path_parts and "word" not in qp:
            if len(numeric_ids) == 1:
                cate_id = numeric_ids[0]
                return f"https://www.yodobashi.com/?word=&cate={cate_id}&sorttyp={yodo_sort}&page={page_num}"

            qp["page"] = [str(page_num)]
            qp["sorttyp"] = [yodo_sort]
            return urlunparse((
                parsed.scheme, parsed.netloc, parsed.path,
                parsed.params, urlencode(qp, doseq=True), parsed.fragment,
            ))

        qp["page"] = [str(page_num)]
        qp["sorttyp"] = [yodo_sort]
        return urlunparse((
            parsed.scheme, parsed.netloc, parsed.path,
            parsed.params, urlencode(qp, doseq=True), parsed.fragment,
        ))

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

        logger.info(f"[YodobashiScraper] scrape_products 開始。base_url={base_url}, ページ={start_page}-{end_page}, 最大={max_items}件")
        count = 0
        skip_jans = skip_jans or []

        for page_num in range(start_page, end_page + 1):
            if count >= max_items:
                break

            target_url = self._build_listing_url(base_url, page_num, sort_order)
            logger.info(f"[Yodobashi] Starting fetch. Page {page_num}: {target_url}")
            yield {"type": "log", "msg": f"📄 ヨドバシ ページ {page_num} を読み込み中... ({target_url[:50]}...)"}

            try:
                await self.page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                await self._safe_wait(3, 5)
                # Human-like scrolling
                await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight/4)")
                await self._safe_wait(0.5, 1.5)
                await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight/2)")
                await self._safe_wait(0.5, 1.5)
            except Exception as e:
                logger.error(f"Page load error: {e}")
                yield {"type": "log", "msg": f"⚠️ ページ {page_num} の読み込みに失敗。リトライ中..."}
                # Retry once with a longer wait
                try:
                    await self._safe_wait(3, 5)
                    await self.page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                    await self._safe_wait(4, 6)
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

                logger.info(f"[Yodobashi] Scraping candidate [{count + 1}/{max_items}]: {item_url}")
                yield {"type": "log", "msg": f"🔍 商品を分析中... ({count + 1}/{max_items})"}

                try:
                    await self.page.goto(item_url, wait_until="domcontentloaded", timeout=30000)
                    await self._safe_wait(1, 3)
                    await self.page.evaluate("window.scrollTo(0, 400)")
                    await self._safe_wait(0.5, 1.5)
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

                count += 1

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

    async def get_stats(self, url):
        try:
            target_url = self._build_listing_url(url, 1, "new_arrival")
            await self.page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
            
            # Extract count from ".resCnt" or similar (e.g. "1,234件中")
            total_items = 0
            for sel in [".resCnt", ".num", ".cnt"]:
                elem = self.page.locator(sel).first
                if await elem.count() > 0:
                    txt = await elem.text_content()
                    digits = re.sub(r'[^\d]', '', txt)
                    if digits:
                        total_items = int(digits)
                        break

            product_links = await self.page.locator('a[href*="/product/"]').all()
            items_per_page = len({
                ((await el.get_attribute("href")) or "").split("?")[0]
                for el in product_links
                if await el.get_attribute("href")
            })

            if total_items == 0:
                body_text = await self.page.locator("body").text_content()
                if body_text:
                    count_candidates = [
                        int(candidate.replace(",", ""))
                        for candidate in re.findall(r"([0-9][0-9,]*)\s*件", body_text)
                    ]
                    if count_candidates:
                        total_items = max(count_candidates)

            if total_items == 0 and items_per_page:
                total_items = items_per_page
            
            return {
                "total_items": total_items,
                "items_per_page": items_per_page or 20,
            }
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return None


    async def stop(self):
        for attr_name in ("page", "context", "browser"):
            target = getattr(self, attr_name, None)
            if not target:
                continue
            try:
                await asyncio.wait_for(target.close(), timeout=5)
            except Exception:
                pass
            finally:
                setattr(self, attr_name, None)

        if self.playwright:
            try:
                await asyncio.wait_for(self.playwright.stop(), timeout=5)
            except Exception:
                pass
            finally:
                self.playwright = None
