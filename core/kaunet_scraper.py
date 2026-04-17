import asyncio
import logging
import random
import re
from collections import deque
from typing import AsyncGenerator
from urllib.parse import urlparse, urlunparse

from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class KaunetScraper:
    def __init__(self, headless=False):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def _safe_wait(self, min_sec=2.0, max_sec=4.5):
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
        logger.info("[Kaunet] 公開商品ページのみを低速で巡回します")
        return "guest"

    def _is_goods_url(self, url):
        return "/kaunet/goods/" in url

    def _is_variation_url(self, url):
        return "/rakuraku/variation/" in url

    def _is_category_url(self, url):
        return "/rakuraku/category/" in url

    def _build_listing_url(self, base_url, page_num):
        if not self._is_category_url(base_url):
            return base_url

        parsed = urlparse(base_url)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 5 and parts[:3] == ["rakuraku", "category", "0"] and parts[3].isdigit():
            parts[3] = str(page_num)
            rebuilt_path = "/" + "/".join(parts) + "/"
            return urlunparse((
                parsed.scheme,
                parsed.netloc,
                rebuilt_path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            ))
        return base_url

    async def _safe_scroll_listing(self):
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.25)")
        await self._safe_wait(0.6, 1.2)
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.55)")
        await self._safe_wait(0.6, 1.2)

    async def _collect_candidate_urls(self):
        links = await self.page.locator(
            '.item_js_root a[href*="/kaunet/goods/"], .item_js_root a[href*="/rakuraku/variation/"]'
        ).all()
        seen = set()
        urls = []
        for el in links:
            href = await el.get_attribute("href")
            if not href:
                continue
            full = href if href.startswith("http") else f"https://www.kaunet.com{href}"
            full = full.split("#")[0]
            if full in seen:
                continue
            seen.add(full)
            urls.append(full)
        return urls

    async def _collect_goods_links_from_variation(self):
        links = await self.page.locator(
            'a.js-goods-link, a[href*="/kaunet/goods/"]'
        ).all()
        seen = set()
        goods_urls = []
        for el in links:
            href = await el.get_attribute("href")
            if not href or "/kaunet/goods/" not in href:
                continue
            full = href if href.startswith("http") else f"https://www.kaunet.com{href}"
            full = full.split("#")[0]
            if full in seen:
                continue
            seen.add(full)
            goods_urls.append(full)
        return goods_urls

    def _find_jan(self, text):
        if not text:
            return ""
        preferred = re.search(r"(?<!\d)(4[59]\d{11})(?!\d)", text)
        if preferred:
            return preferred.group(1)
        generic = re.search(r"(?<!\d)(\d{13})(?!\d)", text)
        return generic.group(1) if generic else ""

    async def _extract_title(self):
        for sel in [
            'span.item_name [itemprop="name"]',
            '.item_name [itemprop="name"]',
            'span.item_name',
            "h1",
        ]:
            try:
                el = self.page.locator(sel).first
                if await el.count() == 0:
                    continue
                text = await el.text_content()
                if text and text.strip():
                    return text.strip()
            except Exception:
                continue
        return "不明"

    async def _extract_price(self):
        for sel in [
            '[itemprop="price"]',
            '.tbl_order_item_price_tax',
            '.tbl_refine_item_price',
            '.price_tax',
        ]:
            try:
                el = self.page.locator(sel).first
                if await el.count() == 0:
                    continue
                text = await el.text_content()
                digits = re.sub(r"[^\d]", "", text or "")
                if digits:
                    return int(digits)
            except Exception:
                continue

        try:
            body = await self.page.locator("body").text_content()
            if body:
                match = re.search(r"([0-9][0-9,]{2,})\s*円", body)
                if match:
                    return int(match.group(1).replace(",", ""))
        except Exception:
            pass
        return 0

    async def _extract_brand(self):
        for sel in [
            '[itemprop="brand"] a',
            "#goods_brand_span a",
            "a.GA_goods_maker_02",
            "a.GA_variation_maker_01",
        ]:
            try:
                el = self.page.locator(sel).first
                if await el.count() == 0:
                    continue
                text = await el.text_content()
                if text and text.strip():
                    return text.strip()
            except Exception:
                continue
        return "不明"

    async def _extract_jan(self):
        for sel in [
            "#spec_box",
            ".specArea",
            ".itemDetailSpec",
            "dl",
            "table",
            "body",
        ]:
            try:
                elements = self.page.locator(sel)
                limit = min(await elements.count(), 8)
                for idx in range(limit):
                    text = await elements.nth(idx).text_content()
                    jan = self._find_jan(text or "")
                    if jan:
                        return jan
            except Exception:
                continue
        return ""

    def _normalize_url(self, url):
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        if path:
            path = f"{path}/"
        return urlunparse((
            parsed.scheme or "https",
            parsed.netloc or "www.kaunet.com",
            path,
            parsed.params,
            "",
            "",
        ))

    def _category_parts(self, url):
        parsed = urlparse(url)
        return [part for part in parsed.path.split("/") if part]

    def _category_depth(self, url):
        parts = self._category_parts(url)
        if len(parts) < 5 or parts[:3] != ["rakuraku", "category", "0"]:
            return 0
        return max(len(parts) - 4, 0)

    def _top_category_code(self, url):
        parts = self._category_parts(url)
        if len(parts) < 5 or parts[:3] != ["rakuraku", "category", "0"]:
            return None
        return parts[4]

    def _category_key(self, url):
        if not self._is_category_url(url):
            return self._normalize_url(url)

        parsed = urlparse(url)
        parts = self._category_parts(url)
        if len(parts) >= 5 and parts[:3] == ["rakuraku", "category", "0"] and parts[3].isdigit():
            parts[3] = "1"
        path = "/" + "/".join(parts) + "/"
        return urlunparse((
            parsed.scheme or "https",
            parsed.netloc or "www.kaunet.com",
            path,
            parsed.params,
            "",
            "",
        ))

    async def _visit_url(self, target_url):
        await self.page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
        await self._safe_wait(2.5, 4.5)

    async def _collect_subcategory_urls(self, current_url):
        current_key = self._category_key(current_url)
        current_depth = self._category_depth(current_url)
        top_code = self._top_category_code(current_url)
        links = await self.page.locator('a[href*="/rakuraku/category/"]').all()
        seen = set()
        subcategory_urls = []

        for el in links:
            href = await el.get_attribute("href")
            if not href:
                continue
            full = href if href.startswith("http") else f"https://www.kaunet.com{href}"
            normalized = self._category_key(full)
            if normalized == current_key:
                continue
            if not self._is_category_url(normalized):
                continue
            if top_code and self._top_category_code(normalized) != top_code:
                continue
            if self._category_depth(normalized) <= current_depth:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            subcategory_urls.append(normalized)

        return subcategory_urls

    async def _extract_last_page_from_current(self):
        max_page = 1
        links = await self.page.locator('ul.paging_list a[href*="/rakuraku/category/"]').all()
        for el in links:
            href = await el.get_attribute("href")
            if not href:
                continue
            full = href if href.startswith("http") else f"https://www.kaunet.com{href}"
            parts = self._category_parts(full)
            if len(parts) >= 5 and parts[:3] == ["rakuraku", "category", "0"] and parts[3].isdigit():
                max_page = max(max_page, int(parts[3]))
        return max_page

    async def _estimate_category_total_from_current(self):
        item_cards = await self.page.locator(".item_js_root").count()
        if item_cards:
            body_text = await self.page.locator("body").text_content()
            if body_text:
                matches = [
                    int(match.replace(",", ""))
                    for match in re.findall(r"([0-9][0-9,]*)\s*件", body_text)
                ]
                if matches:
                    return max(matches), item_cards
            return item_cards, item_cards

        count_texts = await self.page.locator("span.count").all_text_contents()
        count_values = []
        for text in count_texts:
            digits = re.sub(r"[^\d]", "", text or "")
            if digits:
                count_values.append(int(digits))
        if count_values:
            return sum(count_values), 50

        return 0, 50

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
                "id": jan_code or f"kaunet_{page_num}_{index}",
                "jan": jan_code,
                "title": title,
                "brand": brand,
                "price": price,
                "ms_url": item_url,
                "page": page_num,
                "index": index,
                "points_rate": 0,
            },
        }

    async def scrape_products(
        self,
        base_url="https://www.kaunet.com/rakuraku/category/0/1/001/001004/",
        start_page=1,
        end_page=1,
        sort_order="default",
        max_items=100,
        skip_jans=None,
        full_category_mode=False,
    ) -> AsyncGenerator[dict, None]:
        del sort_order

        skip_jans = skip_jans or []
        count = 0
        visited_goods_urls = set()
        visited_category_urls = set()
        queued_category_urls = set()
        unlimited_mode = full_category_mode and max_items >= 1_000_000
        display_limit = "全件" if unlimited_mode else str(max_items)

        yield {
            "type": "log",
            "msg": "🛡️ カウネットは公開ページのみを、間隔を空けながら低速で巡回します。",
        }

        if self._is_goods_url(base_url):
            yield {"type": "log", "msg": "📦 カウネット商品ページを直接確認しています。"}
            await self._visit_url(base_url)
            item = await self._extract_item_from_current_page(base_url, 1, 1, skip_jans=skip_jans)
            if item:
                yield item
            return

        if self._is_variation_url(base_url):
            yield {"type": "log", "msg": "📚 カウネットのバリエーション一覧を開いています。"}
            await self._visit_url(base_url)
            goods_urls = await self._collect_goods_links_from_variation()
            for goods_url in goods_urls:
                if count >= max_items:
                    break
                if goods_url in visited_goods_urls:
                    continue
                visited_goods_urls.add(goods_url)
                await self._visit_url(goods_url)
                item = await self._extract_item_from_current_page(goods_url, 1, count + 1, skip_jans=skip_jans)
                if not item:
                    continue
                count += 1
                yield item
                await self._safe_wait(1.2, 2.4)
            return

        category_queue = deque([self._category_key(base_url)])
        queued_category_urls.add(self._category_key(base_url))

        while category_queue and count < max_items:
            current_category_url = category_queue.popleft()
            current_category_key = self._category_key(current_category_url)
            if current_category_key in visited_category_urls:
                continue
            visited_category_urls.add(current_category_key)

            page_num = 1 if full_category_mode else max(start_page, 1)
            last_page = 1 if full_category_mode else max(end_page, page_num)

            while page_num <= last_page and count < max_items:
                target_url = self._build_listing_url(current_category_url, page_num)
                yield {
                    "type": "log",
                    "msg": f"📄 カウネット ページ {page_num} を読み込み中...（安全配慮でゆっくり巡回）",
                }

                try:
                    await self._visit_url(target_url)
                    await self._safe_scroll_listing()
                except Exception as exc:
                    logger.error("[Kaunet] page load error: %s", exc)
                    yield {"type": "log", "msg": f"⚠️ カウネット ページ {page_num} の読み込みに失敗しました。"}
                    break

                candidate_urls = await self._collect_candidate_urls()
                if candidate_urls:
                    if full_category_mode and page_num == 1:
                        last_page = max(last_page, await self._extract_last_page_from_current())

                    yield {
                        "type": "log",
                        "msg": f"👀 カウネットで {len(candidate_urls)} 件の候補URLを確認しました。",
                    }

                    for candidate_url in candidate_urls:
                        if count >= max_items:
                            break

                        if self._is_goods_url(candidate_url):
                            if candidate_url in visited_goods_urls:
                                continue
                            visited_goods_urls.add(candidate_url)
                            try:
                                await self._visit_url(candidate_url)
                            except Exception:
                                continue
                            item = await self._extract_item_from_current_page(
                                candidate_url,
                                page_num,
                                count + 1,
                                skip_jans=skip_jans,
                            )
                            if not item:
                                continue
                            count += 1
                            yield item
                            await self._safe_wait(1.2, 2.4)
                            continue

                        if self._is_variation_url(candidate_url):
                            try:
                                await self._visit_url(candidate_url)
                            except Exception:
                                continue

                            goods_urls = await self._collect_goods_links_from_variation()
                            if not goods_urls:
                                continue

                            remaining = "全件" if unlimited_mode else min(len(goods_urls), max_items - count)
                            yield {
                                "type": "log",
                                "msg": f"🔎 バリエーションを安全に展開中...（{remaining}件まで確認）",
                            }

                            for goods_url in goods_urls:
                                if count >= max_items:
                                    break
                                if goods_url in visited_goods_urls:
                                    continue
                                visited_goods_urls.add(goods_url)
                                try:
                                    await self._visit_url(goods_url)
                                except Exception:
                                    continue
                                item = await self._extract_item_from_current_page(
                                    goods_url,
                                    page_num,
                                    count + 1,
                                    skip_jans=skip_jans,
                                )
                                if not item:
                                    continue
                                count += 1
                                yield item
                                await self._safe_wait(1.2, 2.4)

                    page_num += 1
                    continue

                subcategory_urls = await self._collect_subcategory_urls(current_category_url)
                if subcategory_urls:
                    yield {
                        "type": "log",
                        "msg": f"🧭 カテゴリ配下の {len(subcategory_urls)} 件の下位カテゴリを順番に確認します。",
                    }
                    for subcategory_url in subcategory_urls:
                        subcategory_key = self._category_key(subcategory_url)
                        if subcategory_key in visited_category_urls or subcategory_key in queued_category_urls:
                            continue
                        queued_category_urls.add(subcategory_key)
                        category_queue.append(subcategory_key)
                    break

                yield {
                    "type": "log",
                    "msg": f"⚠️ カウネットで商品候補を取得できませんでした。({display_limit}件設定)",
                }
                break

    async def get_stats(self, url):
        if self._is_goods_url(url) or self._is_variation_url(url):
            return {"total_items": 1, "items_per_page": 1}

        try:
            target_url = self._build_listing_url(url, 1)
            await self.page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            await self._safe_wait(2.0, 3.0)
            total_items, items_per_page = await self._estimate_category_total_from_current()

            return {
                "total_items": total_items,
                "items_per_page": items_per_page or 50,
            }
        except Exception as exc:
            logger.error("[Kaunet] stats error: %s", exc)
            return None

    async def stop(self):
        try:
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass
