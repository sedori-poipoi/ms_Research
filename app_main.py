import os
import asyncio
import json
import logging
import time
from collections import Counter, defaultdict
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
from core.skater_scraper import SkaterScraper
from core.amazon_api import AmazonSPAPI
from core.keepa_api import KeepaAPI
from core.config_manager import ConfigManager
from core.history_manager import HistoryManager
from core.database import ResearchDatabase
from core.matcher import ProductMatcher
from core.site_config import (
    get_category_map,
    get_default_categories,
    get_site_config,
    serialize_site_configs,
)
from core.env_security import load_env_file
from core.keepa_csv_import import load_keepa_csv_from_bytes

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
    "last_reset_time": 0,  # リセット時刻の初期値
    "keepa_csv": {
        "loaded": False,
        "filename": "",
        "total_rows": 0,
        "indexed_eans": 0,
        "loaded_at": None,
        "file_size_bytes": 0,
    },
    "run_summary": [],
    "site_report": [],
    "condition_presets": [],
}

keepa_csv_store = {
    "by_ean": {},
    "meta": {},
}

runtime_state = {
    "active_scraper": None,
}

KEEPA_CSV_CACHE_DIR = os.path.join("data", "keepa_csv_cache")
KEEPA_CSV_CACHE_FILE = os.path.join(KEEPA_CSV_CACHE_DIR, "latest_keepa.csv")
KEEPA_CSV_CACHE_META_FILE = os.path.join(KEEPA_CSV_CACHE_DIR, "latest_keepa_meta.json")


def persist_keepa_csv_cache(file_bytes, filename, loaded_meta):
    os.makedirs(KEEPA_CSV_CACHE_DIR, exist_ok=True)
    with open(KEEPA_CSV_CACHE_FILE, "wb") as csv_file:
        csv_file.write(file_bytes)

    cache_meta = {
        "filename": filename or loaded_meta.get("filename", ""),
        "file_size_bytes": len(file_bytes),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(KEEPA_CSV_CACHE_META_FILE, "w", encoding="utf-8") as meta_file:
        json.dump(cache_meta, meta_file, ensure_ascii=False, indent=2)


def restore_keepa_csv_cache():
    if not os.path.exists(KEEPA_CSV_CACHE_FILE):
        return False

    try:
        with open(KEEPA_CSV_CACHE_FILE, "rb") as csv_file:
            file_bytes = csv_file.read()

        persisted_meta = {}
        if os.path.exists(KEEPA_CSV_CACHE_META_FILE):
            with open(KEEPA_CSV_CACHE_META_FILE, "r", encoding="utf-8") as meta_file:
                persisted_meta = json.load(meta_file)

        loaded = load_keepa_csv_from_bytes(
            file_bytes,
            filename=persisted_meta.get("filename", ""),
        )
        keepa_csv_store["by_ean"] = loaded["by_ean"]
        keepa_csv_store["meta"] = loaded["meta"]
        session_data["keepa_csv"] = {
            "loaded": bool(loaded["by_ean"]),
            "filename": loaded["meta"].get("filename", ""),
            "total_rows": loaded["meta"].get("total_rows", 0),
            "indexed_eans": loaded["meta"].get("indexed_eans", 0),
            "loaded_at": loaded["meta"].get("loaded_at"),
            "file_size_bytes": persisted_meta.get("file_size_bytes", len(file_bytes)),
        }
        logger.info(
            "Restored Keepa CSV cache: %s (%s rows indexed)",
            session_data["keepa_csv"]["filename"] or "latest_keepa.csv",
            session_data["keepa_csv"]["indexed_eans"],
        )
        return True
    except Exception as exc:
        logger.warning("Failed to restore Keepa CSV cache: %s", exc)
        return False


def upsert_session_result(result):
    """Keep one in-memory row per logical result so UI toggles stay stable."""
    for idx, existing in enumerate(session_data["results"]):
        if existing.get("id") == result.get("id"):
            session_data["results"][idx] = {**existing, **result}
            return
    session_data["results"].append(result)


class ResearchParams(BaseModel):
    target_site: str = "makeup"  # "makeup" / "yodobashi" / "netsea" / "kaunet" / "skater"
    target_sites: List[str] = []
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
    match_mode: str = "realtime"

class BrandUpdate(BaseModel):
    brand: str

class StatusUpdate(BaseModel):
    status: bool


class BulkFavoriteUpdate(BaseModel):
    ids: List[str]

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


def calculate_roi_and_judgment_with_csv_fees(
    buy_box,
    purchase_price,
    fba_fee,
    referral_fee,
    points_rate=0,
):
    if buy_box <= 0 or purchase_price <= 0:
        return 0, 0, 0, "判定不可"

    est_referral_fee = referral_fee if referral_fee > 0 else buy_box * 0.10
    est_fba_fee = fba_fee if fba_fee > 0 else 500 + (buy_box * 0.05)

    points_value = purchase_price * points_rate
    cost = purchase_price - points_value
    profit = buy_box - est_referral_fee - est_fba_fee - cost
    margin = profit / buy_box if buy_box > 0 else 0
    roi = profit / cost if cost > 0 else 0

    judgment = "❌ 利益なし"
    if profit >= 500 and roi >= 0.15:
        judgment = "💎 神・利益"
    elif profit >= 200 and roi >= 0.10:
        judgment = "✅ 準・利益"
    elif profit > 0:
        judgment = "🤔 利益薄"

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


def _safe_int(value, default=0):
    try:
        text = str(value or "").strip()
        digits = "".join(ch for ch in text if ch.isdigit() or ch == "-")
        if not digits or digits == "-":
            return default
        return int(digits)
    except (TypeError, ValueError):
        return default


def _monthly_sales_to_int(value):
    text = str(value or "").strip()
    if not text or text in {"—", "データなし"}:
        return 0

    direct = _safe_int(text, default=0)
    if direct > 0:
        return direct

    if "激売れ" in text:
        return 100
    if "好調" in text:
        return 30
    if "普通" in text:
        return 5
    return 0


def _site_display_name(site_key):
    return get_site_config(site_key).get("display_name", site_key or "不明サイト")


def _instantiate_scraper(site_key, monitor_mode=False):
    if site_key == "yodobashi":
        from core.yodobashi_scraper import YodobashiScraper as SiteScraper
        return SiteScraper(headless=not monitor_mode)
    if site_key == "netsea":
        from core.netsea_scraper import NetseaScraper as SiteScraper
        return SiteScraper(headless=not monitor_mode)
    if site_key == "kaunet":
        from core.kaunet_scraper import KaunetScraper as SiteScraper
        return SiteScraper(headless=not monitor_mode)
    if site_key == "skater":
        from core.skater_scraper import SkaterScraper as SiteScraper
        return SiteScraper(headless=not monitor_mode)
    from core.scraper import MakeUpSolutionScraper as SiteScraper
    return SiteScraper(headless=not monitor_mode)


def _restriction_bucket(row):
    restriction = str(row.get("restriction") or "")
    code = str(row.get("restriction_code") or "")
    if "出品可能" in restriction:
        return "eligible"
    if code == "APPROVAL_REQUIRED":
        return "approval"
    if code == "NOT_ELIGIBLE":
        return "not_eligible"
    return "other"


def _match_meta(match_method, match_score=0, note=""):
    labels = {
        "keepa_csv": "Keepa CSV一致",
        "jan_verified": "JAN一致 + タイトル検証",
        "keyword_verified": "キーワード近似一致",
        "amazon_unmatched": "Amazon未一致",
        "jan_blocked": "JAN候補を除外",
    }
    confidence = "低"
    if match_score >= 95:
        confidence = "高"
    elif match_score >= 75:
        confidence = "中"
    elif match_method == "amazon_unmatched":
        confidence = "未一致"

    label = labels.get(match_method, "照合情報なし")
    details = note.strip()
    if match_method == "keepa_csv" and not details:
        details = "JAN/EAN完全一致のため、CSV側ASINを採用"
    elif match_method == "jan_verified" and not details:
        details = "JAN検索候補をタイトル/容量で再検証"
    elif match_method == "keyword_verified" and not details:
        details = "ブランド + 商品名で近似一致"
    elif match_method == "amazon_unmatched" and not details:
        details = "Amazon候補が見つからず採用見送り"
    elif match_method == "jan_blocked" and not details:
        details = "JAN候補は見つかったが内容不一致のため除外"

    return {
        "match_method": match_method,
        "match_label": f"{label} ({confidence})" if confidence != "未一致" else label,
        "match_details": details,
        "match_score": int(match_score or 0),
    }


def _build_watch_reason(profit, monthly_sales, roi, sellers, price_stability):
    if profit < 0 or profit >= 100:
        return ""

    reasons = []
    monthly_sales_value = _monthly_sales_to_int(monthly_sales)
    if monthly_sales_value >= 30:
        reasons.append(f"月販{monthly_sales_value}で回転が強い")
    elif monthly_sales_value >= 10:
        reasons.append(f"月販{monthly_sales_value}で再確認価値あり")

    roi_percent = max(int((roi or 0) * 100), 0)
    if roi_percent >= 8:
        reasons.append(f"ROI{roi_percent}%で黒字圏に近い")
    if 0 < sellers <= 5:
        reasons.append(f"出品者{sellers}人で競合が重すぎない")
    if price_stability in {"安定", "CSV参照"}:
        reasons.append("価格が比較的安定")

    if not reasons and profit >= 0:
        reasons.append("薄利だが赤字ではないため監視候補")
    return " / ".join(reasons[:3])


def _build_history_comparison(previous_row, current_row):
    if not previous_row:
        return {
            "previous_profit": 0,
            "profit_delta": 0,
            "previous_amazon_price": 0,
            "amazon_price_delta": 0,
            "previous_restriction": "",
            "change_summary": "今回初回",
        }

    previous_profit = _safe_int(previous_row.get("profit"), 0)
    previous_amazon_price = _safe_int(previous_row.get("amazon_price"), 0)
    current_profit = _safe_int(current_row.get("profit"), 0)
    current_amazon_price = _safe_int(current_row.get("amazon_price"), 0)
    previous_restriction = str(previous_row.get("restriction") or "")
    current_restriction = str(current_row.get("restriction") or "")

    profit_delta = current_profit - previous_profit
    amazon_price_delta = current_amazon_price - previous_amazon_price
    summary_parts = []

    if profit_delta > 0:
        summary_parts.append(f"利益 +{profit_delta}円")
    elif profit_delta < 0:
        summary_parts.append(f"利益 {profit_delta}円")

    if amazon_price_delta > 0:
        summary_parts.append(f"Amazon価格 +{amazon_price_delta}円")
    elif amazon_price_delta < 0:
        summary_parts.append(f"Amazon価格 {amazon_price_delta}円")

    if previous_restriction and previous_restriction != current_restriction:
        summary_parts.append("出品制限に変化")

    if previous_profit < 100 <= current_profit:
        summary_parts.append("利益品へ昇格")
    elif previous_profit < 0 <= current_profit:
        summary_parts.append("赤字脱出")

    return {
        "previous_profit": previous_profit,
        "profit_delta": profit_delta,
        "previous_amazon_price": previous_amazon_price,
        "amazon_price_delta": amazon_price_delta,
        "previous_restriction": previous_restriction,
        "change_summary": " / ".join(summary_parts[:3]) if summary_parts else "前回比ほぼ同水準",
    }


def build_run_summary(results, match_mode="realtime"):
    total = len(results)
    matched = [row for row in results if row.get("asin") not in {None, "", "—"}]
    profit_items = [row for row in results if _safe_int(row.get("profit"), 0) >= 100]
    watch_items = [row for row in results if 0 <= _safe_int(row.get("profit"), -1) < 100]
    filtered = [row for row in results if row.get("filter_status") == "filtered"]
    approval_items = [row for row in results if _restriction_bucket(row) == "approval"]

    method_counts = Counter(row.get("match_method") or "unknown" for row in results)
    top_filter_reasons = Counter(
        str(row.get("filter_reason") or "").strip()
        for row in filtered
        if str(row.get("filter_reason") or "").strip()
    )
    top_profit_titles = [
        row.get("title", "不明")
        for row in sorted(results, key=lambda item: _safe_int(item.get("profit"), 0), reverse=True)[:3]
    ]

    cards = [
        {
            "label": "巡回件数",
            "value": str(total),
            "subtext": f"Amazon一致 {len(matched)}件",
        },
        {
            "label": "利益品",
            "value": f"{len(profit_items)}件",
            "subtext": "利益100円以上",
        },
        {
            "label": "監視候補",
            "value": f"{len(watch_items)}件",
            "subtext": "0〜99円の薄利黒字",
        },
        {
            "label": "除外要因",
            "value": top_filter_reasons.most_common(1)[0][0] if top_filter_reasons else "なし",
            "subtext": f"上位: {top_filter_reasons.most_common(1)[0][1]}件" if top_filter_reasons else "除外なし",
        },
        {
            "label": "最優先確認",
            "value": top_profit_titles[0] if top_profit_titles else "まだなし",
            "subtext": "今回の最上位候補",
        },
    ]

    if match_mode in {"keepa_csv", "all_sites_csv"}:
        cards.insert(1, {
            "label": "CSV一致率",
            "value": f"{(len([row for row in matched if row.get('match_method') == 'keepa_csv']) / total * 100):.1f}%" if total else "0.0%",
            "subtext": "Keepa CSVから採用",
        })

    highlights = [
        f"JAN/CSV一致 {method_counts.get('keepa_csv', 0) + method_counts.get('jan_verified', 0)}件",
        f"キーワード一致 {method_counts.get('keyword_verified', 0) }件",
        f"申請入口あり {len(approval_items)}件",
    ]

    return {
        "cards": cards[:6],
        "highlights": [text for text in highlights if not text.endswith(" 0件")],
    }


def build_site_report(results):
    grouped = defaultdict(list)
    for row in results:
        site_label = row.get("source_site_label") or _site_display_name(row.get("source_site"))
        grouped[site_label].append(row)

    report = []
    for site_label, rows in grouped.items():
        total = len(rows)
        matched = sum(1 for row in rows if row.get("asin") not in {None, "", "—"})
        profit = sum(1 for row in rows if _safe_int(row.get("profit"), 0) >= 100)
        watch = sum(1 for row in rows if 0 <= _safe_int(row.get("profit"), -1) < 100)
        filtered = sum(1 for row in rows if row.get("filter_status") == "filtered")
        report.append({
            "site_label": site_label,
            "total": total,
            "match_rate": round((matched / total * 100), 1) if total else 0,
            "profit_rate": round((profit / total * 100), 1) if total else 0,
            "watch_count": watch,
            "filtered_count": filtered,
        })

    return sorted(report, key=lambda row: (row["profit_rate"], row["match_rate"], row["total"]), reverse=True)


def build_condition_presets():
    return [
        {"value": "standard", "label": "標準", "description": "利益100円以上を中心に確認"},
        {"value": "high_rotation", "label": "高回転重視", "description": "薄利でも回転の良いものを優先"},
        {"value": "high_profit", "label": "高利益重視", "description": "利益額とROIが高いものに絞る"},
        {"value": "approval_focus", "label": "申請入口チェック", "description": "利益あり + 申請導線ありを確認"},
        {"value": "custom_saved", "label": "保存した条件", "description": "自分で保存した条件を再利用"},
    ]


def refresh_dashboard(match_mode="realtime"):
    session_data["run_summary"] = build_run_summary(session_data["results"], match_mode=match_mode)
    session_data["site_report"] = build_site_report(session_data["results"])
    session_data["condition_presets"] = build_condition_presets()
    session_data["recommendations"] = generate_recommendations(
        session_data["results"],
        session_data["opportunity_loss"],
        match_mode=match_mode,
        keepa_csv_meta=session_data.get("keepa_csv", {}),
    )


restore_keepa_csv_cache()


def generate_recommendations(results, opportunity_loss, match_mode="realtime", keepa_csv_meta=None):
    recommendations = []
    keepa_csv_meta = keepa_csv_meta or {}

    if opportunity_loss:
        brand, loss = max(opportunity_loss.items(), key=lambda item: item[1])
        recommendations.append({
            "type": "restriction",
            "title": "制限解除で伸ばせるブランド",
            "message": f"ブランド「{brand}」は制限解除で約{int(loss)}円ぶんの利益回収余地があります。",
        })

    profitable_items = [
        row for row in results
        if _safe_int(row.get("profit"), 0) >= 100 and row.get("filter_status") == "visible"
    ]
    if profitable_items:
        top_item = max(profitable_items, key=lambda row: _safe_int(row.get("profit"), 0))
        recommendations.append({
            "type": "profit",
            "title": "まず見に行くべき利益品",
            "message": f"いちばん利益が大きいのは「{top_item.get('title', '不明')}」で、見込み利益は約{_safe_int(top_item.get('profit'))}円です。",
        })

    watch_candidates = [
        row for row in results
        if 0 <= _safe_int(row.get("profit"), -999999) < 100
        and _monthly_sales_to_int(row.get("monthly_sales")) >= 20
    ]
    if watch_candidates:
        recommendations.append({
            "type": "watch",
            "title": "監視候補あり",
            "message": f"0〜99円でも回転が良い商品が{len(watch_candidates)}件あります。値下がりやポイント増加の監視向きです。",
        })

    if match_mode in {"keepa_csv", "all_sites_csv"}:
        csv_unmatched = [row for row in results if row.get("filter_reason") == "CSV未一致"]
        if csv_unmatched:
            filename = keepa_csv_meta.get("filename") or "Keepa CSV"
            matched_rows = [
                row for row in results
                if row.get("asin") not in {None, "", "—"} and row.get("filter_reason") != "CSV未一致"
            ]
            comparable_count = len(matched_rows) + len(csv_unmatched)
            match_rate = (len(matched_rows) / comparable_count * 100) if comparable_count > 0 else 0
            csv_quality = ""
            total_rows = _safe_int(keepa_csv_meta.get("total_rows"), 0)
            indexed_eans = _safe_int(keepa_csv_meta.get("indexed_eans"), 0)
            if total_rows > 0:
                coverage_rate = indexed_eans / total_rows * 100
                csv_quality = f"CSV側は {total_rows}件中{indexed_eans}件がEAN付きで、品質は良好({coverage_rate:.1f}%)です。"

            unmatched_brand_counts = {}
            for row in csv_unmatched:
                brand = str(row.get("brand") or "").strip() or "不明"
                unmatched_brand_counts[brand] = unmatched_brand_counts.get(brand, 0) + 1
            top_unmatched_brands = ", ".join(
                brand for brand, _ in sorted(unmatched_brand_counts.items(), key=lambda item: item[1], reverse=True)[:3]
            )
            brand_hint = f"未一致が多いブランドは {top_unmatched_brands} です。" if top_unmatched_brands else ""

            recommendations.append({
                "type": "csv_gap",
                "title": "CSV未一致が出ています",
                "message": (
                    f"{filename} では 一致{len(matched_rows)}件 / 未一致{len(csv_unmatched)}件 "
                    f"(一致率 {match_rate:.1f}%) でした。"
                    f"{csv_quality} {brand_hint} "
                    f"CSVの収録範囲を広げるか、未一致だけリアルタイム照合へ回すのが有効です。"
                ).strip(),
            })

    if not recommendations and results:
        matched_count = sum(1 for row in results if row.get("asin") not in {None, "", "—"})
        recommendations.append({
            "type": "next_action",
            "title": "次の一手",
            "message": f"今回は{matched_count}件がAmazon照合できています。利益が伸びにくい時は、カテゴリを絞るか照合モードを切り替えると精度が上がりやすいです。",
        })

    return recommendations[:3]

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
        if params.match_mode in {"keepa_csv", "all_sites_csv"} and not keepa_csv_store["by_ean"]:
            log_it("❌ Keepa CSV が未読込です。先に CSV をインポートしてください。")
            return

        all_sites_csv_mode = params.match_mode == "all_sites_csv"
        sites_to_run = list(dict.fromkeys(params.target_sites or [])) if all_sites_csv_mode else [params.target_site]
        if not sites_to_run:
            log_it("❌ 対象サイトが未選択です。")
            return

        estimated_total_items = 0
        total_items_known = True
        session_data["total_items"] = 0
        total_processed = 0
        site_count = len(sites_to_run)

        for site_index, active_site in enumerate(sites_to_run, start=1):
            if not session_data["is_running"]:
                break

            if all_sites_csv_mode:
                selected_cats = get_default_categories(active_site)
                if not selected_cats:
                    log_it(f"⚪️ {_site_display_name(active_site)} は既定カテゴリ未設定のためスキップしました。")
                    continue
                log_it(f"🌐 サイト {site_index}/{site_count}: {_site_display_name(active_site)} をCSV照合で巡回します")
            elif params.custom_url and params.custom_url.strip().startswith("http"):
                selected_cats = ["__custom__"]
                log_it("🎯 カスタムURL優先モード: チェックボックスを無視してこのURLだけをリサーチします")
                logger.info(f"[CustomURL] 対象URL: {params.custom_url.strip()}")
            else:
                selected_cats = params.categories if params.categories else [params.category]
                if not selected_cats or selected_cats == [None]:
                    log_it("❌ ジャンル未選択です。ジャンルを選ぶか、カスタムURLを指定してください。")
                    return

            scraper = _instantiate_scraper(active_site, monitor_mode=params.monitor_mode)
            runtime_state["active_scraper"] = scraper
            log_it(f"🚀 {_site_display_name(active_site)} リサーチ開始")

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
                if not session_data["is_running"]:
                    break

            # --------------- Determine target URL for this category ---------------
                target_url = None
                cat_label = current_cat
                if params.custom_url and params.custom_url.strip().startswith("http") and not all_sites_csv_mode:
                    target_url = params.custom_url.strip()
                    cat_label = "カスタムURL"
                
                if not target_url:
                    cat_data = get_category_map(active_site).get(current_cat, ("不明", ""))
                    cat_label, target_url = cat_data[0], cat_data[1]

                if not target_url:
                    continue

                logger.info(f"[Research] サイト: {active_site} | カテゴリー: {cat_label} | 対象URL: {target_url}")
                log_it(f"📂 [{_site_display_name(active_site)}] カテゴリー [{cat_label}] のリサーチを開始します")

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
                    elif active_site == "kaunet":
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
                if active_site == "kaunet":
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
                            match_method = "amazon_unmatched"
                            match_score = 0
                            match_note = ""
                            
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
                            jan_match_blocked = False
                            csv_match = None

                            if params.match_mode in {"keepa_csv", "all_sites_csv"} and jan and jan.strip().isdigit():
                                csv_match = keepa_csv_store["by_ean"].get(jan.strip())
                                if csv_match:
                                    asin = csv_match["asin"]
                                    amz_brand = csv_match["brand"]
                                    amz_title = csv_match["title"]
                                    sales_rank = f"{csv_match['sales_rank']}位" if csv_match["sales_rank"] > 0 else "圏外"
                                    match_method = "keepa_csv"
                                    match_score = 100
                                    match_note = "Keepa CSVのEAN列と卸JANが完全一致"
                                    log_it(f"🎯 CSV一致: JAN {jan} → ASIN {asin}", is_focus_skip=True)
                                else:
                                    log_it(f"⚪️ CSV未一致: JAN {jan}", is_focus_skip=True)

                            if not asin and jan and jan.strip().isdigit() and len(jan.strip()) >= 8 and params.match_mode not in {"keepa_csv", "all_sites_csv"}:
                                jan_candidates = await amazon.search_by_jan(jan)
                                if jan_candidates:
                                    verified_jan_match = ProductMatcher.find_best_match(item, jan_candidates)
                                    if verified_jan_match:
                                        asin = verified_jan_match["asin"]
                                        amz_brand = verified_jan_match["brand"]
                                        amz_title = verified_jan_match.get("title", "")
                                        sales_rank = verified_jan_match.get("sales_rank", "圏外")
                                        match_method = "jan_verified"
                                        match_score = verified_jan_match.get("match_score", 95)
                                        match_note = f"JAN候補を再検証して採用 (score {match_score})"
                                        log_it(f"🎯 JAN照合成功: {jan} → ASIN {asin}", is_focus_skip=True)
                                    else:
                                        jan_match_blocked = True
                                        match_method = "jan_blocked"
                                        match_score = 0
                                        match_note = "JAN候補は見つかったがタイトル/容量が一致せず除外"
                                        log_it(f"⚠️ JAN候補不一致: {jan} はタイトル照合で弾きました", is_focus_skip=True)
                            
                            allow_keyword_fallback = not jan_match_blocked
                            if active_site == "skater" and jan and jan.strip().isdigit():
                                allow_keyword_fallback = False

                            if not asin and allow_keyword_fallback and params.match_mode not in {"keepa_csv", "all_sites_csv"}:
                                search_query = f"{item['brand']} {item['title'][:40]}"
                                candidates = await amazon.search_by_keyword(search_query, item["brand"])
                                
                                best_match = ProductMatcher.find_best_match(item, candidates)
                                if best_match:
                                    asin = best_match["asin"]
                                    amz_brand = best_match["brand"]
                                    amz_title = best_match.get("title", "")
                                    sales_rank = best_match.get("sales_rank", "圏外")
                                    match_method = "keyword_verified"
                                    match_score = best_match.get("match_score", 70)
                                    match_note = f"ブランド + 商品名の近似一致 (score {match_score})"
                                    log_it(f"✅ KeyWord照合: ASIN {asin} ({sales_rank})", is_focus_skip=True)
                                else:
                                    match_method = "amazon_unmatched"
                                    match_score = 0
                                    match_note = "Amazon候補が見つからず採用見送り"
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
                            
                            if params.match_mode in {"keepa_csv", "all_sites_csv"} and csv_match:
                                buy_box = (
                                    csv_match["buy_box_price"]
                                    or csv_match["new_price"]
                                    or csv_match["amazon_price"]
                                )
                                amazon_listing_price = buy_box
                                amazon_shipping = 0
                                seller_count = csv_match["seller_count"]
                                fba_fee = csv_match["fba_pick_pack_fee"]
                                restriction = "CSV照合モード"
                                restriction_code = ""
                                approval_url = ""
                            elif asin and asin != "—":
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
                            if params.match_mode in {"keepa_csv", "all_sites_csv"} and csv_match:
                                profit, margin, roi, judgment = calculate_roi_and_judgment_with_csv_fees(
                                    buy_box,
                                    item["price"],
                                    fba_fee,
                                    csv_match["referral_fee"],
                                    points_rate=pts_rate,
                                )
                            else:
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

                            if params.match_mode in {"keepa_csv", "all_sites_csv"} and csv_match:
                                monthly_sales = str(csv_match["monthly_sales"]) if csv_match["monthly_sales"] > 0 else "データなし"
                                drops_30 = 0
                                price_stability = "CSV参照"
                            elif asin and asin != "—" and profit > 0 and not no_brand_item:
                                log_it(f"🔍 Keepa精査中: {asin} (トークン残: {keepa.get_tokens_left()})", is_focus_skip=True)
                                keepa_data = await keepa.get_product_data(asin)
                                session_data["keepa_tokens"] = keepa.get_tokens_left()
                                
                                if keepa_data and keepa_data.get("source") == "keepa":
                                    monthly_sales = keepa_data.get("monthly_sales", "データなし")
                                    drops_30 = keepa_data.get("drops_30", 0)
                                    price_stability = keepa_data.get("price_stability", "不明")
                                    keepa_sellers = keepa_data.get("new_offer_count", 0)
                                    if keepa_sellers > 0 and seller_count == 0:
                                        seller_count = keepa_sellers
                            else:
                                if not asin or asin == "—":
                                    monthly_sales = "データなし"
                                elif profit <= 0:
                                    monthly_sales = "—"

                            display_brand = merge_display_brand(item["brand"], amz_brand)

                            # ============================================
                            # STAGE 4: 自動フィルタリング
                            # ============================================
                            in_stock = item.get("in_stock", True)
                            if not in_stock and profit > 0 and asin and asin != "—":
                                judgment = "⚠️ 利益あり(在庫切)"
                                log_it(f"✨ 利益発見！(在庫切): {item['title'][:15]}...")
                            elif profit > 0:
                                log_it(f"✨ 利益発見！ +{int(profit)}円 ({item['title'][:15]}...)")
                            
                            if not asin or asin == "—":
                                judgment = "⚪️ Amazon不一致"
                                filter_status = "filtered"
                                filter_reason = "CSV未一致" if params.match_mode in {"keepa_csv", "all_sites_csv"} else "Amazon未検出"

                            if no_brand_item:
                                filter_status = "filtered"
                                filter_reason = "ノーブランド品"
                            
                            if asin and asin != "—" and roi < 0.10 and profit > 0:
                                filter_status = "filtered"
                                filter_reason = f"利益率{int(roi*100)}% (10%未満)"
                            
                            if price_stability == "⚠️ 高騰中":
                                filter_status = "filtered"
                                filter_reason = "価格高騰中 (値崩れリスク)"

                            watch_reason = _build_watch_reason(
                                int(profit),
                                monthly_sales,
                                roi,
                                seller_count,
                                price_stability,
                            )

                            match_meta = _match_meta(
                                match_method,
                                match_score=match_score,
                                note=match_note,
                            )

                            # ============================================
                            # STAGE 5: 結果の保存と表示
                            # ============================================
                            if asin and asin != "—":
                                amazon_url = csv_match["amazon_url"] if csv_match and csv_match.get("amazon_url") else f"https://www.amazon.co.jp/dp/{asin}"
                                keepa_url = csv_match["keepa_url"] if csv_match and csv_match.get("keepa_url") else f"https://keepa.com/#!product/5-{asin}"
                            else:
                                search_term = jan if jan and jan != "—" else item["title"][:30]
                                amazon_url = f"https://www.amazon.co.jp/s?k={quote(search_term)}"
                                keepa_url = "#"

                            base_entry = {
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
                                "source_site": active_site,
                                "source_site_label": _site_display_name(active_site),
                                "source_category": current_cat,
                                "source_category_label": cat_label,
                                "watch_reason": watch_reason,
                                **match_meta,
                            }

                            previous_row = db.find_matching_result(base_entry)
                            history_meta = _build_history_comparison(previous_row, base_entry)

                            res_entry = {
                                **base_entry,
                                **history_meta,
                            }
                            
                            if session_data.get("last_reset_time", 0) > task_start_time:
                                logger.info("Reset detected. Aborting save for current item.")
                                break

                            try:
                                res_entry = db.save_result(res_entry)
                            except Exception as db_err:
                                logger.error(f"DB save error: {db_err}")
                            
                            upsert_session_result(res_entry)
                            refresh_dashboard(match_mode=params.match_mode)
                            
                            _item_elapsed = time.time() - _item_start
                            _processing_times.append(_item_elapsed)
                            if len(_processing_times) > 5:
                                _processing_times.pop(0)
                            session_data["avg_time_per_item"] = round(sum(_processing_times) / len(_processing_times), 1)
                            
                            if profit > 0 and "制限" in restriction:
                                log_it(f"⚠️ 利益品ですが出品制限あり: {display_brand}")
                            
                            if "制限" in restriction and profit > 0:
                                b = display_brand or "その他"
                                session_data["opportunity_loss"][b] = session_data["opportunity_loss"].get(b, 0) + profit

                            if jan:
                                try:
                                    history.add_to_history(jan)
                                except:
                                    pass
                        except Exception as e:
                            logger.error(f"Error processing item: {e}")
                            log_it(f"⚠️ 商品処理エラー: {str(e)}")

            if scraper:
                await scraper.stop()
                if runtime_state.get("active_scraper") is scraper:
                    runtime_state["active_scraper"] = None
                scraper = None


        if not session_data["is_running"]:
            return

        # --- Recommendations ---
        # リサーチ開始後にリセットされていたら、アドバイスの上書きを完全に中止する（絶対ゾンビ防止）
        if session_data.get("last_reset_time", 0) > task_start_time:
            logger.info("Clear detected during research run. Skipping recommendation re-population.")
            return

        refresh_dashboard(match_mode=params.match_mode)

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
        runtime_state["active_scraper"] = None


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
    scraper = runtime_state.get("active_scraper")
    if scraper:
        try:
            await scraper.stop()
        except Exception as exc:
            logger.warning("Stop cleanup warning: %s", exc)
        finally:
            runtime_state["active_scraper"] = None
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


@app.get("/keepa-csv/status")
async def get_keepa_csv_status():
    return session_data["keepa_csv"]


@app.post("/keepa-csv/upload")
async def upload_keepa_csv(request: Request):
    file_bytes = await request.body()
    filename = request.headers.get("x-filename", "").strip()

    if not file_bytes:
        return {"status": "error", "message": "CSVファイルが空です。"}

    # Raw body upload avoids JSON bloating and is safer for larger CSV imports.
    max_size_bytes = 40 * 1024 * 1024
    if len(file_bytes) > max_size_bytes:
        return {
            "status": "error",
            "message": "CSVが大きすぎます。40MB以下を目安に分割してください。",
        }

    loaded = load_keepa_csv_from_bytes(file_bytes, filename=filename)
    keepa_csv_store["by_ean"] = loaded["by_ean"]
    keepa_csv_store["meta"] = loaded["meta"]
    persist_keepa_csv_cache(file_bytes, filename, loaded["meta"])
    session_data["keepa_csv"] = {
        "loaded": bool(loaded["by_ean"]),
        "filename": loaded["meta"].get("filename", ""),
        "total_rows": loaded["meta"].get("total_rows", 0),
        "indexed_eans": loaded["meta"].get("indexed_eans", 0),
        "loaded_at": loaded["meta"].get("loaded_at"),
        "file_size_bytes": len(file_bytes),
    }
    refresh_dashboard()
    return {"status": "success", **session_data["keepa_csv"]}

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
    session_data["run_summary"] = build_run_summary([])
    session_data["site_report"] = []
    
    return {"status": "success"}



@app.post("/results/{res_id}/toggle_favorite")
async def toggle_favorite(res_id: str, update: StatusUpdate):
    db.update_result_status(res_id, "favorite", update.status)
    for res in session_data["results"]:
        if res["id"] == res_id:
            res["is_favorite"] = 1 if update.status else 0
            break
    refresh_dashboard()
    return {"status": "success"}

@app.post("/results/{res_id}/toggle_checked")
async def toggle_checked(res_id: str, update: StatusUpdate):
    db.update_result_status(res_id, "checked", update.status)
    for res in session_data["results"]:
        if res["id"] == res_id:
            res["is_checked"] = 1 if update.status else 0
            break
    refresh_dashboard()
    return {"status": "success"}


@app.post("/results/watch/favorite_bulk")
async def bulk_favorite_watch(update: BulkFavoriteUpdate):
    updated = 0
    id_set = set(update.ids or [])
    for res in session_data["results"]:
        if res.get("id") in id_set:
            db.update_result_status(res["id"], "favorite", True)
            res["is_favorite"] = 1
            updated += 1
    refresh_dashboard()
    return {"status": "success", "updated": updated}


@app.post("/results/recheck_no_brand")
async def recheck_no_brand_endpoint():
    summary = await recheck_no_brand_results(limit=500)
    session_data["results"] = db.get_all_results(limit=200)
    refresh_dashboard()
    return {"status": "success", **summary}

@app.delete("/results/{res_id}/delete")
async def delete_result_endpoint(res_id: str):
    db.delete_result(res_id)
    session_data["results"] = [r for r in session_data["results"] if r["id"] != res_id]
    refresh_dashboard()
    return {"status": "success"}


refresh_dashboard()


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("APP_PORT", "8002"))
    uvicorn.run(app, host="0.0.0.0", port=port)
