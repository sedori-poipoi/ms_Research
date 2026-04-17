import os
import asyncio
import json
import logging
import time
from datetime import datetime
from typing import List, Dict, Optional
from urllib.parse import quote

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from core.scraper import MakeUpSolutionScraper
from core.yodobashi_scraper import YodobashiScraper
from core.netsea_scraper import NetseaScraper
from core.kaunet_scraper import KaunetScraper
from core.amazon_api import AmazonSPAPI
from core.keepa_api import KeepaAPI
from core.config_manager import ConfigManager
from core.history_manager import HistoryManager
from core.database import ResearchDatabase
from core.matcher import ProductMatcher
from core.site_config import (
    get_category_map,
    get_default_categories,
    serialize_site_configs,
)
from core.env_security import load_env_file

history = HistoryManager()
db = ResearchDatabase()

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_env_file()

app = FastAPI(title="First Leaf Sedori AI Dashboard Pro")

# Static files and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- Initialize ---
# db is already initialized at top

# In-memory session state
session_data = {
    "is_running": False,
    "progress": 0,
    "current_status": "待機中",
    "results": db.get_all_results(limit=200),
    "logs": [],
    "recommendations": [],
    "login_status": "waiting",
    "opportunity_loss": {},
    "keepa_tokens": 60,
    "current_step": 0,
    "items_processed": 0,
    "total_items": 0,
    "start_time": None,
    "avg_time_per_item": 0,
    "last_reset_time": 0  # リセット時刻の初期値
}


def upsert_session_result(result):
    """Keep one in-memory row per logical result so UI toggles stay stable."""
    for idx, existing in enumerate(session_data["results"]):
        if existing.get("id") == result.get("id"):
            session_data["results"][idx] = {**existing, **result}
            return
    session_data["results"].append(result)


class ResearchParams(BaseModel):
    target_site: str = "makeup"  # "makeup" / "yodobashi" / "netsea" / "kaunet"
    category: Optional[str] = None # For compatibility
    categories: List[str] = []
    max_items: int = 20
    start_page: int = 1
    end_page: int = 1
    sort_order: str = "disp_from_datetime"
    auto_page_mode: bool = True
    full_category_mode: bool = False
    focus_mode: bool = False
    skip_history: bool = True
    monitor_mode: bool = False
    custom_url: Optional[str] = None

class BrandUpdate(BaseModel):
    brand: str

class StatusUpdate(BaseModel):
    status: bool

def calculate_roi_and_judgment(buy_box, purchase_price, fba_fee, points_rate=0):
    if buy_box <= 0 or purchase_price <= 0:
        return 0, 0, 0, "判定不可"
    
    # Referral fee estimation if fba_fee is small (fallback happened)
    # Most categories are 8-15%. Let's assume 10% on buy_box (total price).
    referral_fee = buy_box * 0.10
    
    # If fba_fee was estimated (fallback), add a minimum for shipping costs (e.g. 500 yen)
    # and storage/handling.
    if fba_fee <= 0:
        est_fba_fee = 500 + (buy_box * 0.05) if buy_box > 0 else 500
    else:
        est_fba_fee = fba_fee
        
    net_sales = buy_box
    points_value = purchase_price * points_rate
    cost = purchase_price - points_value
    
    # Actual profit = Total Sale - Referral Fee - FBA Fee - Acquisition Cost
    profit = net_sales - referral_fee - est_fba_fee - cost
    
    margin = profit / buy_box if buy_box > 0 else 0
    roi = profit / cost if cost > 0 else 0
    
    judgment = "❌ 利益なし"
    if profit >= 500 and roi >= 0.15: judgment = "💎 神・利益"
    elif profit >= 200 and roi >= 0.10: judgment = "✅ 準・利益"
    elif profit > 0: judgment = "🤔 利益薄"
    
    return profit, margin, roi, judgment


def is_unknown_brand(brand_name):
    value = str(brand_name or "").strip().lower()
    return value in {"", "不明", "unknown", "—", "-", "netsea"}


def has_no_brand_signal(text):
    value = str(text or "").strip().lower()
    if not value:
        return False

    no_brand_signals = {
        "generic",
        "generic brand",
        "ノーブランド",
        "ノーブランド品",
        "ノーブランド/輸入品",
        "no brand",
    }
    if value in no_brand_signals:
        return True

    return any(signal in value for signal in no_brand_signals)


def is_unlistable_no_brand(source_brand, amazon_brand, source_title="", amazon_title=""):
    source_value = str(source_brand or "").strip().lower()
    amazon_value = str(amazon_brand or "").strip().lower()

    if has_no_brand_signal(source_value) or has_no_brand_signal(amazon_value):
        return True

    if has_no_brand_signal(source_title) or has_no_brand_signal(amazon_title):
        return True

    return is_unknown_brand(source_value) and is_unknown_brand(amazon_value)


def merge_display_brand(source_brand, amazon_brand):
    if is_unknown_brand(source_brand) and not is_unknown_brand(amazon_brand):
        return amazon_brand
    return source_brand


async def recheck_no_brand_results(limit=200):
    amazon = AmazonSPAPI()
    updated = 0
    checked = 0

    for row in db.get_brand_recheck_candidates(limit=limit):
        asin = row.get("asin")
        if not asin or asin == "—":
            continue

        checked += 1
        summary = await amazon.get_catalog_summary(asin)
        amazon_brand = summary.get("brand", "不明")
        amazon_title = summary.get("title", "不明")
        display_brand = merge_display_brand(row.get("brand", "不明"), amazon_brand)

        updates = {}
        if display_brand != row.get("brand"):
            updates["brand"] = display_brand

        if is_unlistable_no_brand(row.get("brand"), amazon_brand, row.get("title", ""), amazon_title):
            updates["filter_status"] = "filtered"
            updates["filter_reason"] = "ノーブランド品"
        elif row.get("filter_reason") == "ノーブランド品":
            updates["filter_status"] = "visible"
            updates["filter_reason"] = ""

        if updates:
            db.update_result_fields(row["id"], updates)
            for item in session_data["results"]:
                if item.get("id") == row["id"]:
                    item.update(updates)
                    break
            updated += 1

    return {"checked": checked, "updated": updated}


async def run_research_task(params: ResearchParams):
    session_data["is_running"] = True
    session_data["logs"] = []
    session_data["opportunity_loss"] = {}
    session_data["progress"] = 0
    session_data["login_status"] = "waiting"
    session_data["current_step"] = 1
    session_data["items_processed"] = 0
    session_data["total_items"] = 0
    session_data["start_time"] = datetime.now().isoformat()
    session_data["avg_time_per_item"] = 0
    
    task_start_time = time.time()
    item_start_time = time.time()
    _processing_times = []

    amazon = AmazonSPAPI()
    keepa = KeepaAPI()
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
        # --- カスタムURLが入力された場合は最優先（チェックボックスを完全無視）---
        if params.custom_url and params.custom_url.strip().startswith("http"):
            selected_cats = ["__custom__"]
            log_it(f"🎯 カスタムURL優先モード: チェックボックスを無視してこのURLだけをリサーチします")
            logger.info(f"[CustomURL] 対象URL: {params.custom_url.strip()}")
        else:
            selected_cats = params.categories if params.categories else [params.category]
            if not selected_cats or selected_cats == [None]:
                selected_cats = get_default_categories(params.target_site)

        planned_category_count = max(len(selected_cats), 1)
        estimated_total_items = 0
        total_items_known = True
        session_data["total_items"] = 0
        total_processed = 0

        # --- Instantiate scraper ---
        if params.target_site == "yodobashi":
            from core.yodobashi_scraper import YodobashiScraper
            scraper = YodobashiScraper(headless=not params.monitor_mode)
            log_it("🚀 ヨドバシ.com リサーチ開始")
        elif params.target_site == "netsea":
            from core.netsea_scraper import NetseaScraper
            scraper = NetseaScraper(headless=not params.monitor_mode)
            log_it("🚀 NETSEA リサーチ開始")
        elif params.target_site == "kaunet":
            from core.kaunet_scraper import KaunetScraper
            scraper = KaunetScraper(headless=not params.monitor_mode)
            log_it("🚀 カウネット リサーチ開始")
        else:
            from core.scraper import MakeUpSolutionScraper
            scraper = MakeUpSolutionScraper(headless=not params.monitor_mode)
            log_it("🚀 MakeUp Solution リサーチ開始")

        await scraper.start()
        log_it("🌐 ログインページへ移動中...")
        login_res = await scraper.login()
        session_data["login_status"] = login_res
        
        if login_res == "success":
            log_it("🔑 ログイン成功（会員価格適用）")
        elif login_res == "guest":
            log_it("👤 ゲストモードでリサーチを続行中")
        else:
            log_it("⚠️ ログインに問題が発生しましたが、ゲストとして続行します")

        for current_cat in selected_cats:
            if not session_data["is_running"]: break

            # --------------- Determine target URL for this category ---------------
            target_url = None
            cat_label = current_cat
            if params.custom_url and params.custom_url.strip().startswith("http"):
                target_url = params.custom_url.strip()
                cat_label = "カスタムURL"
            
            if not target_url:
                cat_data = get_category_map(params.target_site).get(current_cat, ("不明", ""))
                cat_label, target_url = cat_data[0], cat_data[1]

            if not target_url: continue

            logger.info(f"[Research] カテゴリー: {cat_label} | 対象URL: {target_url}")
            log_it(f"📂 カテゴリー [{cat_label}] のリサーチを開始します")

            # --- Pre-search Stats Peek ---
            stats = None
            total = 0
            items_per_page = 50
            effective_start_page = max(params.start_page, 1)
            effective_end_page = max(params.end_page, effective_start_page)
            effective_max_items = max(params.max_items, 1)
            estimated_category_items = effective_max_items

            try:
                stats = await scraper.get_stats(target_url)
                if stats and stats.get("total_items") is not None:
                    total = max(int(stats.get("total_items") or 0), 0)
                    items_per_page = max(int(stats.get("items_per_page") or 0), 1)
            except Exception as e:
                logger.warning(f"Stats peek failed: {e}")

            if params.full_category_mode:
                effective_start_page = 1
                if total > 0:
                    effective_end_page = max(1, (total + items_per_page - 1) // items_per_page)
                    effective_max_items = total
                    estimated_category_items = total
                    log_it(f"📊 [{cat_label}] 全 {total} 件を確認。カテゴリ全件モードで最後まで巡回します。")
                elif params.target_site == "kaunet":
                    effective_end_page = 1
                    effective_max_items = 1_000_000
                    estimated_category_items = 0
                    log_it(f"📊 [{cat_label}] カテゴリ全件モードで下位カテゴリまで順に巡回します。件数は巡回しながら確認します。")
                else:
                    estimated_category_items = 0
                    total_items_known = False
                    log_it(f"ℹ️ [{cat_label}] 全件数を事前取得できないため、今回は手動ページ範囲で進めます。")
            elif params.auto_page_mode:
                effective_start_page = 1
                estimated_category_items = effective_max_items
                planned_pages = max(1, (effective_max_items + items_per_page - 1) // items_per_page)
                effective_end_page = planned_pages
                if total > 0:
                    estimated_category_items = min(total, effective_max_items)
                    effective_end_page = max(1, (estimated_category_items + items_per_page - 1) // items_per_page)
                    log_it(f"📊 [{cat_label}] 全 {total} 件を確認。上限 {estimated_category_items} 件ぶんを自動で約 {effective_end_page} ページ巡回します。")
                else:
                    log_it(f"📊 [{cat_label}] 取得上限 {effective_max_items} 件ぶんを自動で約 {effective_end_page} ページ巡回します。")
            else:
                if total > 0:
                    planned_pages = max(effective_end_page - effective_start_page + 1, 1)
                    estimated_category_items = min(total, effective_max_items)
                    log_it(f"📊 [{cat_label}] 全 {total} 件を確認。手動指定の {planned_pages} ページを巡回します。")

            if total_items_known and estimated_category_items > 0:
                estimated_total_items += estimated_category_items
                session_data["total_items"] = estimated_total_items
            elif params.full_category_mode and estimated_category_items == 0:
                total_items_known = False
                session_data["total_items"] = 0

            # --------------- Scraping loop for this category ---------------
            skip_jans = list(history.history.keys()) if params.skip_history else []
            scrape_kwargs = {
                "base_url": target_url,
                "start_page": effective_start_page,
                "end_page": effective_end_page,
                "sort_order": params.sort_order,
                "max_items": effective_max_items,
                "skip_jans": skip_jans,
            }
            if params.target_site == "kaunet":
                scrape_kwargs["full_category_mode"] = params.full_category_mode

            async for product in scraper.scrape_products(**scrape_kwargs):
                if not session_data["is_running"]:
                    log_it("⏹ リサーチが手動停止されました。")
                    break

                if product["type"] == "log":
                    log_it(product["msg"])
                    continue

                if product["type"] == "item":
                    try:
                        total_processed += 1
                        _item_start = time.time()
                        item = product["data"]
                        jan = item.get("jan")
                        asin = None
                        amz_title = ""
                        sales_rank = "圏外"   # ← Amazon照合失敗時のデフォルト値
                        amz_brand = ""
                        buy_box = 0
                        amazon_listing_price = 0
                        amazon_shipping = 0
                        fba_fee = 0
                        seller_count = 0
                        restriction = "確認中"
                        
                        if session_data["total_items"] > 0:
                            total_items = max(session_data["total_items"], 1)
                            session_data["progress"] = min(int((total_processed / total_items) * 100), 100)
                        else:
                            session_data["progress"] = 0
                        session_data["items_processed"] = total_processed
                        session_data["current_step"] = 2

                        # ============================================
                        # STAGE 1: Amazon照合（JAN優先 → キーワード）
                        # ============================================
                        
                        # Strategy A: JAN code search (most accurate)
                        if jan and jan.strip().isdigit() and len(jan.strip()) >= 8:
                            jan_candidates = await amazon.search_by_jan(jan)
                            if jan_candidates:
                                # JAN match = high confidence, take the first result
                                asin = jan_candidates[0]["asin"]
                                amz_brand = jan_candidates[0]["brand"]
                                amz_title = jan_candidates[0].get("title", "")
                                sales_rank = jan_candidates[0].get("sales_rank", "圏外")
                                log_it(f"🎯 JAN照合成功: {jan} → ASIN {asin}", is_focus_skip=True)
                        
                        # Strategy B: Keyword search (fallback)
                        if not asin:
                            search_query = f"{item['brand']} {item['title'][:40]}"
                            candidates = await amazon.search_by_keyword(search_query, item["brand"])
                            
                            best_match = ProductMatcher.find_best_match(item, candidates)
                            if best_match:
                                asin = best_match["asin"]
                                amz_brand = best_match["brand"]
                                amz_title = best_match.get("title", "")
                                sales_rank = best_match.get("sales_rank", "圏外")
                                log_it(f"✅ KeyWord照合: ASIN {asin} ({sales_rank})", is_focus_skip=True)
                            else:
                                log_it(f"⚪️ Amazon不一致: {item['title'][:15]}...", is_focus_skip=True)

                        # ============================================
                        # STAGE 2: Amazon価格・セラー数取得
                        # ============================================
                        buy_box = 0
                        seller_count = 0
                        fba_fee = 0
                        restriction = "確認中"
                        restriction_code = ""
                        approval_url = ""
                        
                        if asin and asin != "—":
                            pricing = await amazon.get_competitive_pricing(asin)
                            buy_box = pricing["price"]
                            amazon_listing_price = pricing["listing_price"]
                            amazon_shipping = pricing["shipping"]
                            seller_count = pricing["seller_count"]
                            
                            if buy_box <= 0:
                                log_it(f"⚪️ 売価不明: {asin}", is_focus_skip=True)
                            else:
                                fba_fee = await amazon.get_fees_estimate(asin, buy_box)
                            
                            res_data = await amazon.get_listing_restrictions(asin)
                            restriction = res_data["status"]
                            restriction_code = res_data["reason_code"]
                            approval_url = res_data["approval_url"]

                        pts_rate = item.get("points_rate", 0)
                        profit, margin, roi, judgment = calculate_roi_and_judgment(
                            buy_box, item["price"], fba_fee, points_rate=pts_rate
                        )

                        # ============================================
                        # STAGE 3: Keepa精査（利益見込みアリのみ）
                        # ============================================
                        monthly_sales = "データなし"
                        drops_30 = 0
                        price_stability = "不明"
                        filter_status = "visible"
                        session_data["current_step"] = 3
                        filter_reason = ""

                        no_brand_item = is_unlistable_no_brand(item["brand"], amz_brand, item["title"], amz_title)

                        if asin and asin != "—" and profit > 0 and not no_brand_item:
                            # Only query Keepa for promising items (token-saving!)
                            log_it(f"🔍 Keepa精査中: {asin} (トークン残: {keepa.get_tokens_left()})", is_focus_skip=True)
                            keepa_data = await keepa.get_product_data(asin)
                            session_data["keepa_tokens"] = keepa.get_tokens_left()
                            
                            if keepa_data and keepa_data.get("source") == "keepa":
                                monthly_sales = keepa_data.get("monthly_sales", "データなし")
                                drops_30 = keepa_data.get("drops_30", 0)
                                price_stability = keepa_data.get("price_stability", "不明")
                                
                                # Update seller count from Keepa if we got better data
                                keepa_sellers = keepa_data.get("new_offer_count", 0)
                                if keepa_sellers > 0 and seller_count == 0:
                                    seller_count = keepa_sellers
                        else:
                            # No Keepa call for unprofitable items
                            if not asin or asin == "—":
                                monthly_sales = "データなし"
                            elif profit <= 0:
                                monthly_sales = "—"

                        display_brand = merge_display_brand(item["brand"], amz_brand)

                        # ============================================
                        # STAGE 4: 自動フィルタリング
                        # ============================================
                        
                        # Mark Opportunity Loss (profitable but out of stock)
                        in_stock = item.get("in_stock", True)
                        if not in_stock and profit > 0 and asin and asin != "—":
                            judgment = "⚠️ 利益あり(在庫切)"
                            log_it(f"✨ 利益発見！(在庫切): {item['title'][:15]}...")
                        elif profit > 0:
                            log_it(f"✨ 利益発見！ +{int(profit)}円 ({item['title'][:15]}...)")
                        
                        if not asin or asin == "—":
                            judgment = "⚪️ Amazon不一致"
                            filter_status = "filtered"
                            filter_reason = "Amazon未検出"

                        if no_brand_item:
                            filter_status = "filtered"
                            filter_reason = "ノーブランド品"
                        
                        # ROI-based filtering
                        if asin and asin != "—" and roi < 0.10 and profit > 0:
                            filter_status = "filtered"
                            filter_reason = f"利益率{int(roi*100)}% (10%未満)"
                        
                        # Price stability filtering
                        if price_stability == "⚠️ 高騰中":
                            filter_status = "filtered"
                            filter_reason = "価格高騰中 (値崩れリスク)"

                        # ============================================
                        # STAGE 5: 結果の保存と表示
                        # ============================================
                        
                        # Build Amazon URL (fix: always provide a useful link)
                        if asin and asin != "—":
                            amazon_url = f"https://www.amazon.co.jp/dp/{asin}"
                            keepa_url = f"https://keepa.com/#!product/5-{asin}"
                        else:
                            # Fallback: search Amazon by product title or JAN
                            search_term = jan if jan and jan != "—" else item["title"][:30]
                            amazon_url = f"https://www.amazon.co.jp/s?k={quote(search_term)}"
                            keepa_url = "#"

                        res_entry = {
                            "jan": jan or "—",
                            "asin": asin or "—",
                            "title": item["title"],
                            "brand": display_brand,
                            "price": item["price"],
                            "amazon_price": buy_box,
                            "amazon_listing_price": amazon_listing_price,
                            "amazon_shipping": amazon_shipping,
                            "rank": sales_rank or "—",
                            "sellers": seller_count,
                            "profit": int(profit),
                            "margin": f"{int(margin*100)}%",
                            "roi": f"{int(roi*100)}%",
                            "restriction": restriction,
                            "restriction_code": restriction_code,
                            "approval_url": approval_url,
                            "judgment": judgment,
                            "amazon_url": amazon_url,
                            "keepa_url": keepa_url,
                            "ms_url": item["ms_url"],
                            "in_stock": 1 if in_stock else 0,
                            "is_favorite": 0,
                            "is_checked": 0,
                            "monthly_sales": monthly_sales,
                            "drops_30": drops_30,
                            "price_stability": price_stability,
                            "filter_status": filter_status,
                            "filter_reason": filter_reason,
                        }
                        
                        if session_data.get("last_reset_time", 0) > task_start_time:
                            logger.info("Reset detected. Aborting save for current item.")
                            break

                        try:
                            res_entry = db.save_result(res_entry)
                        except Exception as db_err:
                            logger.error(f"DB save error: {db_err}")
                        
                        upsert_session_result(res_entry)
                        
                        # Track processing time for ETA calculation
                        _item_elapsed = time.time() - _item_start
                        _processing_times.append(_item_elapsed)
                        if len(_processing_times) > 5:
                            _processing_times.pop(0)
                        session_data["avg_time_per_item"] = round(sum(_processing_times) / len(_processing_times), 1)
                        
                        if profit > 0 and "制限" in restriction:
                            log_it(f"⚠️ 利益品ですが出品制限あり: {display_brand}")
                        
                        # --- Opportunity Loss Tracking ---
                        if "制限" in restriction and profit > 0:
                            b = display_brand or "その他"
                            session_data["opportunity_loss"][b] = session_data["opportunity_loss"].get(b, 0) + profit

                        # Record history
                        if jan:
                            try:
                                history.add_to_history(jan)
                            except:
                                pass
                    except Exception as e:
                        logger.error(f"Error processing item: {e}")
                        log_it(f"⚠️ 商品処理エラー: {str(e)}")


        if not session_data["is_running"]:
            return

        # --- Recommendations ---
        # リサーチ開始後にリセットされていたら、アドバイスの上書きを完全に中止する（絶対ゾンビ防止）
        if session_data.get("last_reset_time", 0) > task_start_time:
            logger.info("Clear detected during research run. Skipping recommendation re-population.")
            return

        session_data["recommendations"] = []  # 生成前に一度空にする
        if session_data["opportunity_loss"]:
            for brand, loss in sorted(session_data["opportunity_loss"].items(), key=lambda x: x[1], reverse=True)[:3]:
                session_data["recommendations"].append({
                    "brand": brand,
                    "potential_profit": int(loss),
                    "message": f"ブランド「{brand}」の制限を解除すれば、約{int(loss)}円の利益チャンスがあります！",
                })

        session_data["progress"] = 100
        log_it(f"🎉 全てのリサーチが完了しました。(Keepaトークン残: {keepa.get_tokens_left()})")

    except Exception as e:
        logger.error(f"Critical error: {e}", exc_info=True)
        log_it(f"❌ システムエラー: {str(e)}")
    finally:
        session_data["is_running"] = False
        session_data["current_status"] = "待機中"
        session_data["current_step"] = 0
        session_data["start_time"] = None
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
            {"value": k, "label": v[0]} for k, v in get_category_map("yodobashi").items()
        ]
    }


@app.get("/site-configs")
async def get_site_configs():
    return {"sites": serialize_site_configs()}

@app.post("/results/clear")
async def clear_results():
    # リセット時刻を記録して、遅れてやってくるゾンビデータを拒否する
    session_data["last_reset_time"] = time.time()
    session_data["is_running"] = False # バックグラウンドのリサーチ活動を即座に停止

    # データベースのお掃除（お気に入り・チェック済み以外）
    db.clear_all_results()
    
    # サーバー側の「記憶」をすべて新しい箱に入れ替える（Nuclear Reset）
    session_data["results"] = db.get_all_results(limit=200)
    session_data["recommendations"] = []
    session_data["opportunity_loss"] = {}
    session_data["logs"] = []
    session_data["progress"] = 0
    session_data["current_status"] = "待機中"
    
    return {"status": "success"}



@app.post("/results/{res_id}/toggle_favorite")
async def toggle_favorite(res_id: str, update: StatusUpdate):
    db.update_result_status(res_id, "favorite", update.status)
    for res in session_data["results"]:
        if res["id"] == res_id:
            res["is_favorite"] = 1 if update.status else 0
            break
    return {"status": "success"}

@app.post("/results/{res_id}/toggle_checked")
async def toggle_checked(res_id: str, update: StatusUpdate):
    db.update_result_status(res_id, "checked", update.status)
    for res in session_data["results"]:
        if res["id"] == res_id:
            res["is_checked"] = 1 if update.status else 0
            break
    return {"status": "success"}


@app.post("/results/recheck_no_brand")
async def recheck_no_brand_endpoint():
    summary = await recheck_no_brand_results(limit=500)
    session_data["results"] = db.get_all_results(limit=200)
    return {"status": "success", **summary}

@app.delete("/results/{res_id}/delete")
async def delete_result_endpoint(res_id: str):
    db.delete_result(res_id)
    session_data["results"] = [r for r in session_data["results"] if r["id"] != res_id]
    return {"status": "success"}



if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("APP_PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)
