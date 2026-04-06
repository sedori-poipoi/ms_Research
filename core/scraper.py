import os
import asyncio
import logging
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
    def __init__(self, headless=True):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def start(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self.page = await self.context.new_page()

    async def login(self):
        try:
            # 1. トップページを開く
            logger.info("Accessing top page for login check...")
            await self.page.goto("https://www.make-up-solution.com/ec/cmShopTopPage1.html", wait_until="networkidle")
            
            # 2. すでにログイン済みか確認
            if await self.page.locator('a:has-text("ログアウト"), a:has-text("マイページ")').count() > 0:
                logger.info("Already logged in.")
                return True

            # 3. ログインボタンを探してクリック
            login_btn = self.page.locator('a:has-text("ログイン")').first
            if await login_btn.count() > 0:
                logger.info("Clicking login button from header...")
                await login_btn.click()
                await self.page.wait_for_load_state('networkidle')
            else:
                # 直接のログインURLも試す（一応）
                logger.info("Navigating to login URL directly...")
                await self.page.goto("https://www.make-up-solution.com/ec/Login")

            # 4. フォーム入力
            if await self.page.locator('input[name="loginId"]').count() > 0:
                await self.page.fill('input[name="loginId"]', MS_EMAIL)
                await self.page.fill('input[name="password"]', MS_PASSWORD)
                await self.page.click('button[type="submit"]')
                await asyncio.sleep(3)
                
                # 成否確認
                if await self.page.locator('a:has-text("ログアウト"), a:has-text("マイページ")').count() > 0:
                    logger.info("Login successful.")
                    return True
            
            logger.warning("Could not complete login flow. Proceeding as GUEST.")
            return True # 続行を許可
        except Exception as e:
            logger.error(f"Login process error: {e}")
            logger.info("Proceeding as GUEST despite login error.")
            return True # エラーでもリサーチ自体は止めない


    async def scrape_products(self, base_url="https://www.make-up-solution.com/ec/Facet?keyword=メイク", 
                              start_page=1, 
                              end_page=1, 
                              sort_order="disp_from_datetime", 
                              max_items=100,
                              skip_jans=None) -> AsyncGenerator[dict, None]:
        """Scrape products across a range of pages with specific sorting."""
        count = 0
        skip_jans = skip_jans or []
        
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
                await self.page.goto(target_url, wait_until="networkidle")
                await asyncio.sleep(2)
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

                    await self.page.goto(item_url, wait_until="domcontentloaded")
                    await asyncio.sleep(1) 

                    # Title detection
                    title = "不明"
                    title_selectors = ['h1.product-name', '.item_name', 'h2.product-title', 'input[name="productName"]']
                    for sel in title_selectors:
                        elem = self.page.locator(sel).first
                        if await elem.count() > 0:
                            if sel.startswith('input'):
                                title = await elem.get_attribute("value")
                            else:
                                title = await elem.text_content()
                            break
                    
                    # Price detection
                    price = 0
                    price_selectors = ['.buy-box .price-box .price', '.item_price', '.product-price .price', '.price']
                    for sel in price_selectors:
                        elem = self.page.locator(sel).first
                        if await elem.count() > 0:
                            p_str = await elem.text_content()
                            if '¥' in p_str or ',' in p_str:
                                price = int(p_str.replace('¥', '').replace(',', '').replace('税込', '').replace('(税込)', '').strip())
                                break
                    
                    # Brand detection
                    brand = "不明"
                    brand_selectors = ['.brand-name', '.product-brand', '.item_brand']
                    for sel in brand_selectors:
                        elem = self.page.locator(sel).first
                        if await elem.count() > 0:
                            brand = await elem.text_content()
                            break
                    
                    if brand == "不明":
                        # Try breadcrumbs
                        breadcrumbs = await self.page.locator('.breadcrumb, .breadcrumbs').text_content()
                        if breadcrumbs:
                            parts = [p.strip() for p in breadcrumbs.split('/')]
                            if len(parts) > 2:
                                brand = parts[2]

                    yield {
                        "type": "item",
                        "data": {
                            "id": jan_code,
                            "jan": jan_code,
                            "title": title.strip() if title else "不明",
                            "brand": brand.strip() if brand else "不明",
                            "price": price,
                            "ms_url": item_url,
                            "page": page_num,
                            "index": count
                        }
                    }
                    
                except Exception as e:
                    logger.error(f"Error scraping product {item_url}: {e}")
                    continue

    async def stop(self):
        """Cleanup browser resources."""
        try:
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except:
            pass
