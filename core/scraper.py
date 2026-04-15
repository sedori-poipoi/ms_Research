import os
import asyncio
import logging
import re
from urllib.parse import urlparse, parse_qs, urlunparse, urlencode
from typing import AsyncGenerator
from playwright.async_api import async_playwright
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

MS_EMAIL = os.environ.get("MS_EMAIL")
MS_PASSWORD = os.environ.get("MS_PASSWORD")

class MakeUpSolutionScraper:
    def __init__(self, headless=False):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def _safe_wait(self, min_sec=1, max_sec=3):
        """AI検知を避けるためのランダムな待機時間"""
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
        try:
            # 1. トップページを開く
            logger.info("Accessing top page for login check...")
            await self.page.goto("https://www.make-up-solution.com/ec/cmShopTopPage1.html", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2) # Wait a bit for JS to run
            
            # 2. すでにログイン済みか確認
            if await self.page.locator('a:has-text("ログアウト"), a:has-text("マイページ")').count() > 0:
                logger.info("Already logged in.")
                return True

            # 3. ログインボタンを探してクリック
            login_btn = self.page.locator('a:has-text("ログイン")').first
            if await login_btn.count() > 0:
                logger.info("Clicking login button from header...")
                await login_btn.click()
                await self.page.wait_for_load_state('domcontentloaded')
            else:
                # 直接のログインURLも試す（一応）
                logger.info("Navigating to login URL directly...")
                await self.page.goto("https://www.make-up-solution.com/ec/Login", wait_until="domcontentloaded")

            # 4. フォーム入力
            if await self.page.locator('input[name="loginId"]').count() > 0:
                await self.page.fill('input[name="loginId"]', MS_EMAIL)
                await self.page.fill('input[name="password"]', MS_PASSWORD)
                await self.page.click('button[type="submit"]')
                await asyncio.sleep(3)
                
                # 成否確認
                if await self.page.locator('a:has-text("ログアウト"), a:has-text("マイページ")').count() > 0:
                    logger.info("Login successful.")
                    return "success"
            
            logger.warning("Could not complete login flow. Proceeding as GUEST.")
            return "guest"
        except Exception as e:
            logger.error(f"Login process error: {e}")
            logger.info("Proceeding as GUEST despite login error.")
            return "error"


    async def _extract_product_data(self, jan_code, item_url, index):
        """Extract product data from the currently loaded product page.
        
        Selectors verified by JavaScript debugging:
        - Title: h1.headline (NOT h3, which picks up hidden cart modal)
        - Price: .price_wrap .price (NOT bare .price, which picks up header cart ¥0)
        - Brand: #brandUrl1 (official brand link)
        """
        try:
            # Title detection
            title = "不明"
            for sel in ['h1.headline', '.headline', 'h1']:
                try:
                    elem = self.page.locator(sel).first
                    if await elem.count() > 0:
                        title = await elem.text_content()
                        if title and title.strip() and title.strip() != "不明":
                            break
                except Exception:
                    continue
            
            # Price detection
            price = 0
            for sel in ['.price_wrap .price', '.price_wrap > .price']:
                try:
                    elem = self.page.locator(sel).first
                    if await elem.count() > 0:
                        p_str = await elem.text_content()
                        if p_str and any(c.isdigit() for c in p_str):
                            price = int(re.sub(r'[^\d]', '', p_str))
                            if price > 0:
                                break
                except Exception:
                    continue
            
            # Price fallback: find visible p.price in price_wrap/info container
            if price == 0:
                try:
                    price = await self.page.evaluate("""
                        (() => {
                            const els = document.querySelectorAll('p.price');
                            for (const el of els) {
                                if (el.offsetParent !== null) {
                                    const parent = el.parentElement?.className || '';
                                    if (parent.includes('price_wrap') || parent.includes('info')) {
                                        const digits = el.textContent.trim().replace(/[^0-9]/g, '');
                                        if (digits && parseInt(digits) > 0) return parseInt(digits);
                                    }
                                }
                            }
                            return 0;
                        })()
                    """)
                except Exception:
                    pass
            
            # Brand detection ... (existing brand logic)
            brand = "不明"
            try:
                brand_elem = self.page.locator('#brandUrl1').first
                if await brand_elem.count() > 0:
                    raw_brand = await brand_elem.text_content()
                    if raw_brand:
                        brand = raw_brand.strip()
                        brand = re.sub(r'【[^】]*】\s*', '', brand)
                        brand = re.sub(r'[（(][^）)]*[）)]', '', brand)
                        brand = brand.strip()
            except Exception:
                pass
            
            # Stock detection
            in_stock = True
            try:
                # Direct check for 'カートに追加' button text, #cartOn, or input submit.
                # Must be careful not to match random page text.
                btn_locator = self.page.locator('#cartOn, input[value="カートに追加"], button:has-text("カートに追加")').first
                rearrival_locator = self.page.locator('#stockArrivalRegisterSubmitLink, a:has-text("入荷お知らせメール")').first
                
                if await btn_locator.count() > 0:
                    in_stock = True
                elif await rearrival_locator.count() > 0:
                    in_stock = False
                else:
                    # If neither button is found clearly, check the main form area
                    form_area = self.page.locator('.detail_info, form[name="FRM"]').first
                    if await form_area.count() > 0:
                        content = await form_area.text_content()
                        if content and ("品切れ中" in content or "在庫なし" in content or "販売終了" in content):
                            in_stock = False
                    else:
                        in_stock = False
            except Exception:
                pass

            # Fallback brand from breadcrumbs
            if not brand or brand == "不明":
                try:
                    bc = self.page.locator('.breadcrumb, .breadcrumbs, .pankuzu').first
                    if await bc.count() > 0:
                        breadcrumbs = await bc.text_content()
                        if breadcrumbs:
                            parts = [p.strip() for p in breadcrumbs.split('/')]
                            if len(parts) > 2:
                                brand = parts[2]
                except Exception:
                    pass
            
            return {
                "id": jan_code,
                "jan": jan_code,
                "title": title.strip() if title else "不明",
                "brand": brand.strip() if brand else "不明",
                "price": price,
                "ms_url": item_url,
                "in_stock": in_stock,
                "page": 1,
                "index": index
            }
        except Exception as e:
            logger.error(f"Error extracting product data from {item_url}: {e}")
            return None

    async def scrape_products(self, base_url="https://www.make-up-solution.com/ec/Facet?keyword=メイク", 
                              start_page=1, 
                              end_page=1, 
                              sort_order="disp_from_datetime", 
                              max_items=100,
                              skip_jans=None) -> AsyncGenerator[dict, None]:
        """Scrape products across a range of pages with specific sorting."""
        count = 0
        skip_jans = skip_jans or []
        
        # Direct product URL detection: if the URL is a single product page, scrape it directly
        if "/ec/pro/disp/1/" in base_url:
            jan_code = base_url.rstrip('/').split('/')[-1].split('?')[0]
            if jan_code.isdigit() and len(jan_code) >= 5:
                if jan_code not in skip_jans:
                    yield {"type": "log", "msg": f"🎯 商品ページを直接分析します: {jan_code}"}
                    # Navigate and extract data (reuses the same logic below)
                    try:
                        await self.page.goto(base_url, wait_until="networkidle", timeout=30000)
                        await asyncio.sleep(1)
                        
                        product_data = await self._extract_product_data(jan_code, base_url, 1)
                        if product_data:
                            yield {"type": "item", "data": product_data}
                    except Exception as e:
                        logger.error(f"Error scraping direct product URL: {e}")
                        yield {"type": "log", "msg": f"⚠️ 商品ページの読み込みエラー: {e}"}
                return  # Done - it was a single product URL
        
        for page_num in range(start_page, end_page + 1):
            if count >= max_items:
                break
            
            # Smart URL construction
            parsed_url = urlparse(base_url)
            query_params = parse_qs(parsed_url.query)
            query_params['page'] = [str(page_num)]
            query_params['sort'] = [sort_order]
            
            new_query = urlencode(query_params, doseq=True)
            target_url = urlunparse((
                parsed_url.scheme,
                parsed_url.netloc,
                parsed_url.path,
                parsed_url.params,
                new_query,
                parsed_url.fragment
            ))
            
            logger.info(f"Navigating to page {page_num}: {target_url}")
            yield {"type": "log", "msg": f"📄 ページ {page_num} を読み込み中..."}
            
            try:
                await self.page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                await self._safe_wait(2, 4)
                
                # Human-like scrolling
                await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight/3)")
                await self._safe_wait(0.5, 1.5)
                await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight/1.5)")
                await self._safe_wait(0.5, 1.5)
                
            except Exception as e:
                logger.error(f"Page load error ({target_url}): {e}")
                yield {"type": "log", "msg": f"⚠️ ページ {page_num} の読み込みに失敗しました。"}
                continue

            # Find product links (matching /ec/pro/disp/1/JAN)
            links_elements = await self.page.locator('a[href*="/ec/pro/disp/1/"]').all()
            unique_urls = []
            seen_urls = set()
            for l in links_elements:
                href = await l.get_attribute("href")
                if href and "/ec/pro/disp/1/" in href:
                    # Clean URL to base
                    base_href = href.split('?')[0] if '?' in href else href
                    if base_href not in seen_urls:
                        seen_urls.add(base_href)
                        unique_urls.append(base_href)

            actual_urls = []
            for url in unique_urls:
                if url.startswith("http"):
                    actual_urls.append(url)
                else:
                    actual_urls.append(f"https://www.make-up-solution.com{url}")
            
            logger.info(f"Found {len(actual_urls)} unique product URLs on page {page_num}.")
            
            for item_url in actual_urls:
                if count >= max_items:
                    break
                try:
                    jan_code = item_url.split('/')[-1]
                    if not jan_code.isdigit() or len(jan_code) < 5:
                        continue
                    
                    if jan_code in skip_jans:
                        # Silently skip items from history
                        continue
                    
                    count += 1
                    logger.info(f"Scraping product [{count}/{max_items}]: {jan_code}")
                    yield {"type": "log", "msg": f"🔍 商品を分析中... ({count}/{max_items})"}

                    await self.page.goto(item_url, wait_until="networkidle", timeout=30000)
                    await self._safe_wait(1, 2) 
                    
                    # Human-like scrolling behavior
                    await self.page.evaluate("window.scrollTo(0, 300)")
                    await self._safe_wait(0.5, 1.0)

                    product_data = await self._extract_product_data(jan_code, item_url, count)
                    if product_data:
                        yield {
                            "type": "item",
                            "data": product_data
                        }
                    
                except Exception as e:
                    logger.error(f"Error scraping product {item_url}: {e}")
                    continue

    async def get_stats(self, url):
        """Quickly peek at total items and pages for a URL."""
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(1)
            
            # Extract total count from ".total_count", ".resultCount", ".count", or ".facet_total"
            total_items = 0
            for sel in ['.total_count', '.resultCount', '.count', '.facet_total']:
                stats_elem = self.page.locator(sel).first
                if await stats_elem.count() > 0:
                    txt = await stats_elem.text_content()
                    digits = re.sub(r'[^\d]', '', txt)
                    if digits:
                        total_items = int(digits)
                        break
            
            return {
                "total_items": total_items,
                "items_per_page": 50 # Standard for MS is around 48-50
            }
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return None


    async def stop(self):
        """Cleanup browser resources."""
        try:
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except:
            pass
