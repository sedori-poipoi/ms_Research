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

# In-memory session state
session_data = {
    "is_running": False,
    "progress": 0,
    "current_status": "待機中",
    "results": [],
    "logs": [],
    "recommendations": []
}

class ResearchParams(BaseModel):
    category: str = "makeup"
    max_items: int = 20
    start_page: int = 1
    end_page: int = 1
    sort_order: str = "disp_from_datetime"
    focus_mode: bool = False # 即戦力モード (解除済みのみ)
    skip_history: bool = True # 履歴スキップ機能
    custom_url: Optional[str] = None # カスタムURL (任意)

class BrandUpdate(BaseModel):
    brand: str

def calculate_roi_and_judgment(buy_box, purchase_price, fba_fee):
    if buy_box <= 0 or purchase_price <= 0:
        return 0, 0, 0, "✖️ 売価不明"
    
    profit = buy_box - purchase_price - fba_fee
    profit_margin = profit / buy_box
    roi = profit / purchase_price
    
    if profit >= 1000 and roi >= 0.2:
        judgment = "💎 超利益商品"
    elif profit >= 500 and roi >= 0.15:
        judgment = "✨ 利益商品"
    elif profit > 0:
        judgment = "✅ 小利益"
    else:
        judgment = "✖️ 利益薄"
        
    return profit, profit_margin, roi, judgment

async def run_research_task(params: ResearchParams):
    global session_data
    session_data["is_running"] = True
    session_data["results"] = []
    session_data["logs"] = []
    session_data["progress"] = 0
    session_data["current_status"] = "リサーチを開始しています..."
    session_data["recommendations"] = []

    scraper = MakeUpSolutionScraper(headless=True)
    amazon = AmazonSPAPI()
    
    def log_it(msg, is_focus_skip=False):
        if params.focus_mode and is_focus_skip:
            return
        session_data["logs"].insert(0, f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        session_data["current_status"] = msg
        logger.info(msg)

    try:
        # Load Cleared Brands
        cleared_brands = ConfigManager.load_brands()
        cleared_brands_lower = [b.lower() for b in cleared_brands]
        
        category_urls = {
            "makeup": "https://www.make-up-solution.com/ec/Facet?keyword=メイク",
            "skincare": "https://www.make-up-solution.com/ec/Facet?keyword=スキンケア",
            "sale": "https://www.make-up-solution.com/ec/Facet?category_1=11030000000",
            "all": "https://www.make-up-solution.com/ec/Facet?keyword="
        }
        
        target_url = category_urls.get(params.category, category_urls["makeup"])
        if params.custom_url and params.custom_url.strip().startswith("http"):
            target_url = params.custom_url.strip()
            log_it(f"🎯 カスタムURLを検知: {target_url[:50]}...")
        
        excluded_brands = ["セザンヌ", "CEZANNE"]
        opportunity_loss = {}

        await scraper.start()
        log_it("メイクアップソリューションにログイン中...")
        if not await scraper.login():
            log_it("⚠️ ログインに失敗しました。")
            return

        log_it(f"➡️ リサーチ対象: '{params.category}' {'(即戦力モード🔥)' if params.focus_mode else ''}")
        
        skip_jans = []
        if params.skip_history:
            skip_jans = list(history.history.keys())
            log_it(f"ℹ️ 履歴から {len(skip_jans)} 件の既読商品をスキップ対象にしました。")

        # Start Scraping in Real-time
        async for update in scraper.scrape_products(
            target_url, 
            start_page=params.start_page, 
            end_page=params.end_page, 
            sort_order=params.sort_order,
            max_items=params.max_items,
            skip_jans=skip_jans
        ):
            if not session_data["is_running"]: break
            
            if update["type"] == "log":
                log_it(update["msg"])
                continue
            
            if update["type"] == "item":
                item = update["data"]
                idx = item["index"]
                
                # Progress calculation
                progress_val = int((idx / params.max_items) * 100)
                session_data["progress"] = min(progress_val, 100)
                
                brand_name = item['brand'].lower()
                
                # Exclusion Check
                is_excluded = False
                for ex in excluded_brands:
                    if ex.lower() in brand_name or ex.lower() in item['title'].lower():
                        is_excluded = True
                        break
                if is_excluded:
                    log_it(f"⏩ スキップ: {item['brand']} (除外)")
                    continue
                
                # Focus Mode Logic
                is_cleared = False
                for cb in cleared_brands_lower:
                    if cb in brand_name:
                        is_cleared = True
                        break
                
                if params.focus_mode and not is_cleared:
                    continue

                # Amazon Matching
                log_it(f"🔎 照合中: {item['title'][:15]}...")
                jan = item['jan']
                asin, amz_brand = amazon.get_asin_from_jan(jan)
                
                if not asin:
                    log_it(f"⚪️ Amazon未登録: {jan}")
                    continue

                # No-brand filter (Noise detection)
                no_brand_keywords = ["ノーブランド", "no brand", "generic"]
                amz_brand_lower = str(amz_brand).lower()
                if any(k in amz_brand_lower for k in no_brand_keywords):
                    log_it(f"⚪️ ノイズ除去: {amz_brand} (ASIN: {asin})")
                    continue

                buy_box = amazon.get_competitive_pricing(asin)
                if buy_box <= 0:
                    log_it(f"⚪️ Amazon売価不明: {asin}")
                    continue
                
                fba_fee = amazon.get_fees_estimate(asin, buy_box)
                profit, margin, roi, judgment = calculate_roi_and_judgment(buy_box, item['price'], fba_fee)
                restriction = amazon.get_listing_restrictions(asin)

                if "制限" in restriction and profit > 0:
                    brand = item['brand'] if item['brand'] != "不明" else "その他"
                    opportunity_loss[brand] = opportunity_loss.get(brand, 0) + profit

                if profit > 0:
                    res_entry = {
                        "id": f"{jan}_{idx}",
                        "jan": jan,
                        "asin": asin,
                        "title": item['title'],
                        "brand": item['brand'],
                        "price": item['price'],
                        "amazon_price": buy_box,
                        "profit": int(profit),
                        "margin": f"{int(margin*100)}%",
                        "roi": f"{int(roi*100)}%",
                        "restriction": restriction,
                        "judgment": judgment,
                        "amazon_url": f"https://www.amazon.co.jp/dp/{asin}",
                        "keepa_url": f"https://keepa.com/#!product/5-{asin}",
                        "ms_url": item['ms_url']
                    }
                    session_data["results"].append(res_entry)
                    
                    if "制限" not in restriction:
                        log_it(f"✨ 利益発見！: +{int(profit)}円 ({item['title'][:10]})")
                    else:
                        log_it(f"⚠️ 利益品ですが出品制限あり: {item['brand']}")
                else:
                    # 利益が出ない場合も一応ログを出す（静止していると思われないため）
                    if idx % 5 == 0:
                        log_it(f"📉 利益なし: {item['title'][:10]}... 等をスキップ中")
                
                # 履歴に追加
                history.add_to_history(jan)

        # Recommendations
        if opportunity_loss:
            sorted_loss = sorted(opportunity_loss.items(), key=lambda x: x[1], reverse=True)
            for brand, loss in sorted_loss[:3]:
                session_data["recommendations"].append({
                    "brand": brand,
                    "potential_profit": int(loss),
                    "message": f"ブランド「{brand}」の制限を解除すれば、約{int(loss)}円の利益チャンスがあります！"
                })

        log_it("🎉 全てのリサーチが完了しました。")

    except Exception as e:
        logger.error(f"Critical error during research: {str(e)}", exc_info=True)
        log_it(f"❌ システムエラー: {str(e)}")
    finally:
        session_data["is_running"] = False
        session_data["current_status"] = "待機中"
        await scraper.stop()

# --- ROUTES ---
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})

@app.post("/start")
async def start_research(params: ResearchParams, background_tasks: BackgroundTasks):
    if session_data["is_running"]:
        return {"status": "error", "message": "既に実行中です"}
    background_tasks.add_task(run_research_task, params)
    return {"status": "success"}

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
