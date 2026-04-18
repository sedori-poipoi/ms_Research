import asyncio
import logging
import random
import re
from typing import AsyncGenerator
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class SkaterScraper:
    def __init__(self, headless=False):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def _safe_wait(self, min_sec=2.0, max_sec=4.0):
        await asyncio.sleep(random.uniform(min_sec, max_sec))

    async def start(self):
        self.playwright = await async_playwright().start()
        launch_args = [
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
        ]
        if self.headless:
            launch_args.append("--disable-gpu")

        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            channel="chrome",
            args=launch_args,
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
        await self.page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'languages', {
                get: () => ['ja-JP', 'ja', 'en-US', 'en']
            });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            """
        )

    async def login(self):
        logger.info("[Skater] 公開カテゴリと公開商品ページのみを低速で巡回します")
        return "guest"

    def _is_item_url(self, url):
        return "/view/item/" in url

    def _is_category_url(self, url):
        return "/view/category/" in url

    def _build_listing_url(self, base_url, page_num, sort_order="default"):
        del sort_order

        if not self._is_category_url(base_url):
            return base_url

        parsed = urlparse(base_url)
        query = parse_qs(parsed.query)
        if page_num > 1:
            query["page"] = [str(page_num)]
        else:
            query.pop("page", None)
        query.pop("sort", None)

        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(query, doseq=True),
            parsed.fragment,
        ))

    async def _visit_url(self, target_url):
        await self.page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
        await self._safe_wait(2.0, 4.0)

    async def _safe_scroll_listing(self):
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.2)")
        await self._safe_wait(0.5, 1.0)
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.45)")
        await self._safe_wait(0.5, 1.0)

    async def _collect_product_urls(self):
        links = await self.page.locator('a[href*="/view/item/"]').all()
        seen = set()
        product_urls = []
        for el in links:
            href = await el.get_attribute("href")
            if not href or "/view/item/" not in href:
                continue
            full = href if href.startswith("http") else f"https://www.skater-onlineshop.com{href}"
            full = full.split("?")[0]
            if full in seen:
                continue
            seen.add(full)
            product_urls.append(full)
        return product_urls

    def _find_jan(self, text):
        if not text:
            return ""
        preferred = re.search(r"(?<!\d)(4[59]\d{11})(?!\d)", text)
        if preferred:
            return preferred.group(1)
        generic = re.search(r"(?<!\d)(\d{13})(?!\d)", text)
        return generic.group(1) if generic else ""

    async def _extract_title(self):
        for sel in ["h1", ".item_name", ".heading"]:
            try:
                loc = self.page.locator(sel)
                count = await loc.count()
                if count == 0:
                    continue
                candidates = []
                for idx in range(min(count, 3)):
                    text = await loc.nth(idx).text_content()
                    if text and text.strip():
                        clean = text.strip()
                        if clean != "スケーター公式オンラインショップ":
                            candidates.append(clean)
                if candidates:
                    return max(candidates, key=len)
            except Exception:
                continue
        try:
            title = await self.page.title()
            if title and title.strip():
                return title.strip()
        except Exception:
            pass
        return "不明"

    async def _extract_price(self):
        for sel in [".price-sale", ".item__price-wrap", "[class*=price]", "body"]:
            try:
                loc = self.page.locator(sel)
                count = await loc.count()
                if count == 0:
                    continue
                candidate_prices = []
                for idx in range(min(count, 8)):
                    text = await loc.nth(idx).text_content()
                    if not text:
                        continue
                    if sel == "body" and "アイテム説明" in text:
                        text = text.split("アイテム説明", 1)[0]

                    matches = re.findall(r"[￥¥]\s*([0-9,]+)|([0-9,]+)\s*円", text)
                    prices = []
                    for yen_price, 円_price in matches:
                        raw = yen_price or 円_price
                        if not raw:
                            continue
                        value = int(raw.replace(",", ""))
                        if value >= 100:
                            prices.append(value)

                    if not prices:
                        continue

                    if sel == ".price-sale":
                        return prices[0]
                    if sel == ".item__price-wrap":
                        return prices[-1]
                    candidate_prices.extend(prices)

                if candidate_prices:
                    if sel == "[class*=price]":
                        return max(candidate_prices)
                    return candidate_prices[-1]
            except Exception:
                continue
        return 0

    async def _extract_brand(self):
        title = await self._extract_title()
        if "スケーター" in title:
            return "スケーター"

        try:
            body = await self.page.locator("body").text_content()
            if body and "スケーター" in body:
                return "スケーター"
        except Exception:
            pass
        return "スケーター"

    async def _extract_jan(self):
        for sel in [".item_description", ".item-info", "body"]:
            try:
                elements = self.page.locator(sel)
                limit = min(await elements.count(), 4)
                for idx in range(limit):
                    text = await elements.nth(idx).text_content()
                    jan = self._find_jan(text or "")
                    if jan:
                        return jan
            except Exception:
                continue
        return ""

    async def scrape_products(
        self,
        base_url="https://www.skater-onlineshop.com/view/category/lunchbox",
        start_page=1,
        end_page=1,
        sort_order="default",
        max_items=100,
        skip_jans=None,
    ) -> AsyncGenerator[dict, None]:
        skip_jans = skip_jans or []
        count = 0
        visited_urls = set()

        yield {
            "type": "log",
            "msg": "🛡️ スケーター公式は公開カテゴリと公開商品ページだけを、低速で順番に確認します。",
        }

        if self._is_item_url(base_url):
            yield {"type": "log", "msg": "📦 スケーター商品ページを直接確認しています。"}
            await self._visit_url(base_url)
            item = await self._extract_item_from_current_page(base_url, 1, 1, skip_jans=skip_jans)
            if item:
                yield item
            return

        for page_num in range(max(start_page, 1), max(end_page, start_page) + 1):
            if count >= max_items:
                break

            target_url = self._build_listing_url(base_url, page_num, sort_order)
            yield {
                "type": "log",
                "msg": f"📄 スケーター ページ {page_num} を読み込み中...（安全配慮でゆっくり巡回）",
            }

            try:
                await self._visit_url(target_url)
                await self._safe_scroll_listing()
            except Exception as exc:
                logger.error("[Skater] page load error: %s", exc)
                yield {"type": "log", "msg": f"⚠️ スケーター ページ {page_num} の読み込みに失敗しました。"}
                continue

            product_urls = await self._collect_product_urls()
            if not product_urls:
                yield {"type": "log", "msg": "⚠️ スケーターで商品候補を取得できませんでした。"}
                continue

            yield {
                "type": "log",
                "msg": f"👀 スケーターで {len(product_urls)} 件の候補URLを確認しました。",
            }

            for product_url in product_urls:
                if count >= max_items:
                    break
                if product_url in visited_urls:
                    continue
                visited_urls.add(product_url)

                try:
                    await self._visit_url(product_url)
                except Exception:
                    continue

                item = await self._extract_item_from_current_page(
                    product_url,
                    page_num,
                    count + 1,
                    skip_jans=skip_jans,
                )
                if not item:
                    continue

                count += 1
                yield item
                await self._safe_wait(1.2, 2.5)

    async def _extract_item_from_current_page(self, item_url, page_num, index, skip_jans=None):
        title = await self._extract_title()
        price = await self._extract_price()
        brand = await self._extract_brand()
        jan_code = await self._extract_jan()

        if jan_code and skip_jans and jan_code in skip_jans:
            return None
        if price <= 0:
            return None

        return {
            "type": "item",
            "data": {
                "id": jan_code or f"skater_{page_num}_{index}",
                "jan": jan_code,
                "title": title,
                "brand": brand,
                "price": price,
                "ms_url": item_url,
                "page": page_num,
                "index": index,
                "points_rate": 0.01,
            },
        }

    async def get_stats(self, url):
        if self._is_item_url(url):
            return {"total_items": 1, "items_per_page": 1}

        try:
            target_url = self._build_listing_url(url, 1)
            await self.page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            await self._safe_wait(2.0, 3.0)
            items_per_page = await self.page.locator('a[href*="/view/item/"]').count()
            return {
                "total_items": 0,
                "items_per_page": max(min(items_per_page, 60), 1),
            }
        except Exception as exc:
            logger.error("[Skater] stats error: %s", exc)
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
