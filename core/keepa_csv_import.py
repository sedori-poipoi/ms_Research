import csv
import io
import re
from datetime import datetime


def _to_int(value, default=0):
    text = str(value or "").strip()
    if not text:
        return default
    digits = re.sub(r"[^\d-]", "", text.replace(",", ""))
    if digits in {"", "-"}:
        return default
    try:
        return int(digits)
    except ValueError:
        return default


def _to_float(value, default=0.0):
    text = str(value or "").strip()
    if not text:
        return default
    cleaned = re.sub(r"[^\d.\-]", "", text.replace(",", ""))
    if cleaned in {"", "-", ".", "-."}:
        return default
    try:
        return float(cleaned)
    except ValueError:
        return default


def _normalize_ean(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) >= 8 else ""


def _build_row_index(row):
    asin = str(row.get("ASIN", "")).strip()
    ean = _normalize_ean(row.get("商品コード: EAN"))
    if not ean:
        return None

    buy_box_price = _to_int(row.get("Buy Box: 現在価格"))
    new_price = _to_int(row.get("新品: 現在価格"))
    amazon_price = _to_int(row.get("Amazon: 現在価格"))
    monthly_sales = _to_int(row.get("月間売上トレンド: 先月の購入"))
    sales_rank = _to_int(row.get("売れ筋ランキング: 現在価格"))
    seller_count = _to_int(row.get("新品アイテム数: 現在価格"))
    fba_pick_pack_fee = _to_int(row.get("FBA Pick&Pack 料金"))
    referral_fee = _to_int(row.get("現在のBuy Box価格に基づく紹介料"))
    referral_fee_rate = _to_float(row.get("紹介料％"))

    amazon_url = str(row.get("URL: Amazon", "")).strip()
    if not amazon_url and asin:
        amazon_url = f"https://www.amazon.co.jp/dp/{asin}"

    keepa_url = str(row.get("URL: Keepa", "")).strip()
    if not keepa_url and asin:
        keepa_url = f"https://keepa.com/#!product/5-{asin}"

    return {
        "asin": asin or "—",
        "ean": ean,
        "title": str(row.get("商品名", "")).strip() or "不明",
        "brand": str(row.get("ブランド", "")).strip() or "不明",
        "model": str(row.get("モデル", "")).strip(),
        "size": str(row.get("サイズ", "")).strip(),
        "variation_asins": str(row.get("バリエーションASIN", "")).strip(),
        "variation_attributes": str(row.get("バリエーション属性", "")).strip(),
        "buy_box_price": buy_box_price,
        "new_price": new_price,
        "amazon_price": amazon_price,
        "monthly_sales": monthly_sales,
        "sales_rank": sales_rank,
        "seller_count": seller_count,
        "fba_pick_pack_fee": fba_pick_pack_fee,
        "referral_fee": referral_fee,
        "referral_fee_rate": referral_fee_rate,
        "root_category": str(row.get("カテゴリ: ルート", "")).strip(),
        "sub_category": str(row.get("カテゴリ: サブ", "")).strip(),
        "amazon_url": amazon_url,
        "keepa_url": keepa_url,
    }


def load_keepa_csv_from_bytes(file_bytes, filename=""):
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    by_ean = {}
    total_rows = 0
    indexed_rows = 0

    for row in reader:
        total_rows += 1
        indexed = _build_row_index(row)
        if not indexed:
            continue
        by_ean[indexed["ean"]] = indexed
        indexed_rows += 1

    return {
        "by_ean": by_ean,
        "meta": {
            "filename": filename,
            "total_rows": total_rows,
            "indexed_eans": indexed_rows,
            "loaded_at": datetime.now().isoformat(timespec="seconds"),
        },
    }
