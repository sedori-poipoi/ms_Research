import os
import asyncio
import logging
import re
import random
from urllib.parse import urlparse, parse_qs, urlunparse, urlencode
from typing import AsyncGenerator
from playwright.async_api import async_playwright
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

NETSEA_CATEGORIES = {
    "sale":      ("ゲリラセール",     "https://www.netsea.jp/special/guerrilla"),
    "makeup":    ("メイク・コスメ",   "https://www.netsea.jp/search/?category_id=302"),
    "skincare":  ("スキンケア",       "https://www.netsea.jp/search/?category_id=313"),
    "haircare":  ("ヘアケア",         "https://www.netsea.jp/search/?category_id=315"),
    "health":    ("衛生日用品",       "https://www.netsea.jp/search/?category_id=305"),
    "food":      ("食品",             "https://www.netsea.jp/search/?category_id=8"),
    "drink":     ("飲料",             "https://www.netsea.jp/search/?category_id=804"),
    "daily":     ("日用品",           "https://www.netsea.jp/search/?category_id=2"),
}

class NetseaScraper:
    def __init__(self, headless=False):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def _safe_wait(self, min_sec=2, max_sec=5):
        """AI検知を避けるためのランダムな待機時間"""
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
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="ja-JP"
        )
        self.page = await self.context.new_page()
        
        # webdriver検知回避などのステルス化
        await self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'languages', { get: () => ['ja-JP', 'ja', 'en-US', 'en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        """)

    async def login(self):
        netsea_id = os.getenv("NETSEA_ID")
        netsea_pwd = os.getenv("NETSEA_PASSWORD")

        if not netsea_id or not netsea_pwd:
            logger.warning("[NETSEA] ログイン情報が設定されていません")
            return "guest"

        logger.info("[NETSEA] ログイン処理を開始します")
        try:
            await self.page.goto("https://www.netsea.jp/login", wait_until="domcontentloaded")
            await self._safe_wait(2, 4)

            # Look for login form elements
            await self.page.fill('input#userId', netsea_id)
            await self._safe_wait(1, 2)
            await self.page.fill('input#pass', netsea_pwd)
            await self._safe_wait(1, 2)
            
            # Click submit
            await self.page.click('button:has-text("ログインする"), button.btnType01')
            await self.page.wait_for_load_state("domcontentloaded")
            await self._safe_wait(3, 5)

            logger.info("[NETSEA] ログイン完了")
            return "success"
        except Exception as e:
            logger.error(f"[NETSEA] ログイン中にエラー: {e}")
            return "guest"

    async def scrape_products(
        self,
        base_url="https://www.netsea.jp/search/?category_id=302",
        start_page=1,
        end_page=1,
        sort_order="new_arrival",
        max_items=100,
        skip_jans=None,
    ) -> AsyncGenerator[dict, None]:
        
        count = 0
        skip_jans = skip_jans or []

        # Map sort types
        sort_map = {
            "new_arrival": "2",
            "price_asc": "3",
            "price_desc": "4",
            "disp_from_datetime": "2",
            "selling_price0_min": "3",
            "selling_price0_max": "4",
        }
        netsea_sort = sort_map.get(sort_order, "2")

        for page_num in range(start_page, end_page + 1):
            if count >= max_items:
                break

            parsed = urlparse(base_url)
            qp = parse_qs(parsed.query)
            qp["page"] = [str(page_num)]
            qp["sort"] = [netsea_sort]
            target_url = urlunparse((
                parsed.scheme, parsed.netloc, parsed.path,
                parsed.params, urlencode(qp, doseq=True), parsed.fragment,
            ))

            yield {"type": "log", "msg": f"📄 NETSEA ページ {page_num} を読み込み中... (安全に進行中)"}
            try:
                await self.page.goto(target_url, wait_until="domcontentloaded")
                await self._safe_wait(3, 5) # ランダム待機
            except Exception as e:
                yield {"type": "log", "msg": f"⚠️ ページ読み込みエラー: {e}"}
                continue

            # スクロールして画像や遅延要素をロードしつつ人間らしさを演出
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight/3)")
            await self._safe_wait(1, 2)
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight/1.5)")
            await self._safe_wait(1, 2)
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await self._safe_wait(1, 2)

            # NETSEAのリストアイテム取得
            item_els = await self.page.locator('.showcaseType01, .item, .listItem, .itemLine, li.module-item, .product-box, .box').all()
            
            if not item_els:
                yield {"type": "log", "msg": "⚠️ 商品情報が見つかりません。"}
                continue
                
            yield {"type": "log", "msg": f"👀 ページ内に約{len(item_els)}件のアイテムを発見。"}

            for i in range(len(item_els)):
                if count >= max_items:
                    break
                    
                item_el = item_els[i]
                try:
                    txt = await item_el.text_content()
                    html = await item_el.inner_html()
                except:
                    continue
                
                # URLとJANコード抽出 (商品詳細URLの末尾がJANのケースが多い)
                url = target_url
                jan = ""
                # "shop/店舗ID/商品ID(またはJAN)"の形式を捕捉
                url_match = re.search(r"href=\"([^\"]+/shop/\d+/[^\"]+)\"", html)
                if url_match:
                    url_path = url_match.group(1)
                    url = f"https://www.netsea.jp{url_path}" if url_path.startswith("/") else url_path
                    # URLの末尾13桁を調べる
                    potential_jan = url_path.split("/")[-1]
                    if re.match(r"4[59]\d{11}$", potential_jan):
                        jan = potential_jan
                
                # HTML内から(First Leaf拡張機能などによる)明確なテキストを拾う
                if not jan:
                    jan_match = re.search(r"(4[59]\d{11})", txt)
                    if jan_match:
                        jan = jan_match.group(1)

                if jan and jan in skip_jans:
                    continue

                # 価格抽出
                price = 0
                price_match = re.search(r"class=\"afterPrice\"[^>]*>\s*([0-9,]+)", html)
                if not price_match:
                    price_match = re.search(r"class=\"price[^>]*>\s*([0-9,]+)", html)
                
                if price_match:
                    price = int(price_match.group(1).replace(",", ""))
                else:
                    m = re.search(r"([0-9,]+)\s*(円|/点|\(税抜\))", txt)
                    if m:
                        price = int(m.group(1).replace(",", ""))
                
                # Title抽出
                title = "商品"
                title_match = re.search(r"class=\"showcaseHd\"[^>]*>.*?<a[^>]*>(.*?)</a>", html, re.IGNORECASE | re.DOTALL)
                if title_match:
                    title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
                else:
                    title_match2 = re.search(r"class=\"name\".*?>(.*?)<", html, re.IGNORECASE)
                    if title_match2:
                        title = re.sub(r'<[^>]+>', '', title_match2.group(1)).strip()
                    else:
                        title_attr = re.search(r"title=\"(.*?)\"", html)
                        if title_attr:
                            title = title_attr.group(1)
                        else:
                            lines = [ln.strip() for ln in txt.split("\n") if ln.strip()]
                            if lines:
                                title = lines[0][:40]

                if price > 0:
                    count += 1
                    yield {
                        "type": "item",
                        "data": {
                            "id": jan or f"netsea_{count}",
                            "jan": jan,
                            "title": title,
                            "brand": "NETSEA",
                            "price": price,
                            "ms_url": url,
                            "page": page_num,
                            "index": count,
                            "points_rate": 0, # NETSEAポイント考慮は一旦0
                        },
                    }
                    await self._safe_wait(0.5, 1.5)

    async def get_stats(self, url):
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await self._safe_wait(2, 4)
            # Find item count text (varies on NETSEA)
            total_items = 0
            body_txt = await self.page.locator("body").text_content()
            m = re.search(r"全\s*([0-9,]+)\s*件", body_txt)
            if m:
                total_items = int(m.group(1).replace(",", ""))
            
            return {
                "total_items": total_items,
                "items_per_page": 20 
            }
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return None

    async def stop(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
