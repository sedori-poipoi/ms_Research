import os
import asyncio
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from dotenv import load_dotenv

from core.scraper import MakeUpSolutionScraper
from core.yodobashi_scraper import YodobashiScraper, YODOBASHI_CATEGORIES
from core.amazon_api import AmazonSPAPI
from core.config_manager import ConfigManager
from core.history_manager import HistoryManager

history = HistoryManager()

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI(title="First Leaf Sedori AI Dashboard Pro")

# Static files and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

from core.database import ResearchDatabase

# --- Initialize ---
db = ResearchDatabase()

# In-memory session state
session_data = {
    "is_running": False,
    "progress": 0,
    "current_status": "待機中",
    "results": db.get_all_results(limit=100), # Load history on start
    "logs": [],
    "recommendations": [],
    "login_status": "waiting",
    "opportunity_loss": {}
}

class ResearchParams(BaseModel):
    target_site: str = "makeup"  # "makeup" or "yodobashi"
    category: str = "makeup"
    max_items: int = 20
    start_page: int = 1
    end_page: int = 1
    sort_order: str = "disp_from_datetime"
    focus_mode: bool = False
    skip_history: bool = True
    custom_url: Optional[str] = None

class BrandUpdate(BaseModel):
    brand: str

def calculate_roi_and_judgment(buy_box, purchase_price, fba_fee, points_rate=0):
    if buy_box <= 0 or purchase_price <= 0:
        return 0, 0, 0, "判定不可"
    
    net_sales = buy_box
    points_value = purchase_price * points_rate
    cost = purchase_price - points_value
    profit = net_sales - cost - fba_fee
    margin = profit / buy_box if buy_box > 0 else 0
    roi = profit / cost if cost > 0 else 0
    
    judgment = "❌ 利益なし"
    if profit > 800 and roi > 0.15: judgment = "💎 神・利益"
    elif profit > 300 and roi > 0.10: judgment = "✅ 準・利益"
    elif profit > 0: judgment = "🤔 利益薄"
    
    return profit, margin, roi, judgment


# ---------- Category URL maps ----------
MS_CATEGORIES = {
    "makeup":   "https://www.make-up-solution.com/ec/Facet?keyword=メイク",
    "skincare": "https://www.make-up-solution.com/ec/Facet?keyword=スキンケア",
    "sale":     "https://www.make-up-solution.com/ec/Facet?category_1=11030000000",
    "all":      "https://www.make-up-solution.com/ec/Facet?keyword=",
}


async def run_research_task(params: ResearchParams):
    session_data["is_running"] = True
    # session_data["results"] = db.get_all_results() # Keep history instead of clearing
    session_data["logs"] = []
    session_data["opportunity_loss"] = {}
    session_data["progress"] = 0
    session_data["login_status"] = "waiting"

    amazon = AmazonSPAPI()
    scraper = None

    def log_it(msg, is_focus_skip=False):
        if params.focus_mode and is_focus_skip:
            return
        session_data["logs"].insert(0, f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        session_data["current_status"] = msg
        if len(session_data["logs"]) > 100:
            session_data["logs"].pop()
        logger.info(msg)

    try:
        # --------------- Determine target URL ---------------
        target_url = None
        if params.custom_url and params.custom_url.strip().startswith("http"):
            target_url = params.custom_url.strip()
            if "yodobashi.com" in target_url:
                params.target_site = "yodobashi"
            elif "make-up-solution" in target_url:
                params.target_site = "makeup"
        
        if not target_url:
            if params.target_site == "yodobashi":
                target_url = YODOBASHI_CATEGORIES.get(params.category, ["パソコン", "https://www.yodobashi.com/category/19531/"])[1]
            else:
                target_url = MS_CATEGORIES.get(params.category, MS_CATEGORIES["makeup"])

        # --------------- Instantiate scraper ---------------
        if params.target_site == "yodobashi":
            from core.yodobashi_scraper import YodobashiScraper
            scraper = YodobashiScraper(headless=False)
            log_it("🚀 ヨドバシ.com リサーチ開始")
        else:
            from core.scraper import MakeUpSolutionScraper
            scraper = MakeUpSolutionScraper(headless=True)
            log_it("🚀 MakeUp Solution リサーチ開始")

        await scraper.start()
        login_res = await scraper.login()
        session_data["login_status"] = login_res
        
        if login_res == "success":
            log_it("🔑 ログイン成功（会員価格適用）")
        elif login_res == "guest":
            log_it("👤 ゲストモードでリサーチを続行中")
        else:
            log_it("⚠️ ログインに問題が発生しましたが、ゲストとして続行します")

        # --------------- Scraping loop ---------------
        skip_jans = [] if params.skip_history else [] 

        idx = 0
        async for product in scraper.scrape_products(
            base_url=target_url,
            start_page=params.start_page,
            end_page=params.end_page,
            sort_order=params.sort_order,
            max_items=params.max_items,
            skip_jans=skip_jans
        ):
            if not session_data["is_running"]:
                log_it("⏹ リサーチが手動停止されました。")
                break

            if product["type"] == "log":
                log_it(product["msg"])
                continue
            
            idx += 1
            item = product["data"]
            jan = item.get("jan")
            asin = None
            amz_brand = None
            
            session_data["progress"] = int((idx / params.max_items) * 100)

            # --- Target search ---
            if not asin:
                # Fallback: AI Matcher + Keyword
                from core.matcher import ProductMatcher
                search_query = f"{item['brand']} {item['title'][:40]}"
                candidates = await amazon.search_by_keyword(search_query, item["brand"])
                best_match = ProductMatcher.find_best_match(item, candidates)
                if best_match:
                    asin = best_match["asin"]
                    amz_brand = best_match["brand"]
                    sales_rank = best_match.get("sales_rank", "圏外")
                    log_it(f"✅ AI照合成功: ASIN {asin} ({sales_rank})", is_focus_skip=True)
                else:
                    log_it(f"⚪️ Amazon不一致: {item['title'][:15]}...", is_focus_skip=True)
                    continue

            # --- Price & Fees (Async) ---
            pricing = await amazon.get_competitive_pricing(asin)
            buy_box = pricing["price"]
            seller_count = pricing["seller_count"]
            
            if buy_box <= 0:
                log_it(f"⚪️ 売価不明: {asin}", is_focus_skip=True)
                continue

            fba_fee = await amazon.get_fees_estimate(asin, buy_box)
            pts_rate = item.get("points_rate", 0)
            profit, margin, roi, judgment = calculate_roi_and_judgment(
                buy_box, item["price"], fba_fee, points_rate=pts_rate
            )
            
            restriction = await amazon.get_listing_restrictions(asin)

            if profit > 0:
                res_entry = {
                    "id": f"{asin}_{idx}",
                    "jan": jan or "—",
                    "asin": asin,
                    "title": item["title"],
                    "brand": item["brand"],
                    "price": item["price"],
                    "amazon_price": buy_box,
                    "rank": sales_rank,
                    "sellers": seller_count,
                    "profit": int(profit),
                    "margin": f"{int(margin*100)}%",
                    "roi": f"{int(roi*100)}%",
                    "restriction": restriction,
                    "judgment": judgment,
                    "amazon_url": f"https://www.amazon.co.jp/dp/{asin}",
                    "keepa_url": f"https://keepa.com/#!product/5-{asin}",
                    "ms_url": item["ms_url"],
                }
                session_data["results"].append(res_entry)
                
                try:
                    db.save_result(res_entry)
                except:
                    pass

                if "制限" not in restriction:
                    log_it(f"✨ 利益発見！ +{int(profit)}円 ({item['title'][:12]}...)")
                else:
                    log_it(f"⚠️ 利益品ですが出品制限あり: {item['brand']}")
            
            # --- Opportunity Loss Tracking ---
            if "制限" in restriction and profit > 0:
                b = item.get("brand", "その他")
                session_data["opportunity_loss"][b] = session_data["opportunity_loss"].get(b, 0) + profit

            # Record history (silent fail if not setup)
            try:
                if jan:
                    history.add_to_history(jan)
            except:
                pass

        # --- Recommendations ---
        if session_data["opportunity_loss"]:
            for brand, loss in sorted(session_data["opportunity_loss"].items(), key=lambda x: x[1], reverse=True)[:3]:
                session_data["recommendations"].append({
                    "brand": brand,
                    "potential_profit": int(loss),
                    "message": f"ブランド「{brand}」の制限を解除すれば、約{int(loss)}円の利益チャンスがあります！",
                })

        log_it("🎉 全てのリサーチが完了しました。")

    except Exception as e:
        logger.error(f"Critical error: {e}", exc_info=True)
        log_it(f"❌ システムエラー: {str(e)}")
    finally:
        session_data["is_running"] = False
        session_data["current_status"] = "待機中"
        if scraper:
            await scraper.stop()


# ====================== ROUTES ======================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})

@app.post("/start")
async def start_research(params: ResearchParams, background_tasks: BackgroundTasks):
    if session_data["is_running"]:
        return {"status": "error", "message": "既に実行中です"}
    background_tasks.add_task(run_research_task, params)
    return {"status": "success"}

@app.post("/stop")
async def stop_research():
    session_data["is_running"] = False
    return {"status": "stopped"}

@app.get("/status")
async def get_status():
    return session_data

@app.get("/events")
async def event_stream(request: Request):
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(session_data)}\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/brands")
async def get_brands():
    return {"brands": ConfigManager.load_brands()}

@app.post("/brands")
async def add_brand(brand_update: BrandUpdate):
    brands = ConfigManager.load_brands()
    if brand_update.brand not in brands:
        brands.append(brand_update.brand)
        ConfigManager.save_brands(brands)
    return {"status": "success"}

@app.delete("/brands/{brand}")
async def delete_brand(brand: str):
    brands = ConfigManager.load_brands()
    if brand in brands:
        brands.remove(brand)
        ConfigManager.save_brands(brands)
    return {"status": "success"}

@app.get("/yodobashi-categories")
async def get_yodobashi_categories():
    """Return Yodobashi categories for the frontend dropdown."""
    return {
        "categories": [
            {"value": k, "label": v[0]} for k, v in YODOBASHI_CATEGORIES.items()
        ]
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
