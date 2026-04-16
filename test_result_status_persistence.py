import sqlite3
import tempfile
from pathlib import Path

from core.database import ResearchDatabase


def make_result(**overrides):
    result = {
        "jan": "4939553041535",
        "asin": "B07XSD7C2Q",
        "title": "セザンヌ パールグロウチーク P2 ベージュコーラル",
        "brand": "セザンヌ",
        "price": 660,
        "amazon_price": 1100,
        "profit": -50,
        "margin": "-4%",
        "roi": "-7%",
        "rank": "圏外",
        "sellers": 1,
        "restriction": "出品可能",
        "judgment": "❌ 利益なし",
        "amazon_url": "https://www.amazon.co.jp/dp/B07XSD7C2Q",
        "keepa_url": "https://keepa.com/#!product/5-B07XSD7C2Q",
        "ms_url": "https://www.make-up-solution.com/ec/pro/disp/1/4939553041535",
        "in_stock": 1,
        "monthly_sales": "—",
        "drops_30": 0,
        "price_stability": "不明",
        "filter_status": "visible",
        "filter_reason": "",
        "restriction_code": "",
        "approval_url": "",
    }
    result.update(overrides)
    return result


def test_status_survives_research_updates():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = ResearchDatabase(str(Path(tmpdir) / "history.db"))

        first = db.save_result(make_result())
        db.update_result_status(first["id"], "favorite", True)
        db.update_result_status(first["id"], "checked", True)

        updated = db.save_result(make_result(price=620, profit=20, roi="3%"))
        rows = db.get_all_results()

        assert updated["id"] == first["id"]
        assert len(rows) == 1
        assert rows[0]["is_favorite"] == 1
        assert rows[0]["is_checked"] == 1
        assert rows[0]["price"] == 620
        assert rows[0]["profit"] == 20


def test_legacy_row_status_is_migrated_to_stable_id():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "history.db"
        db = ResearchDatabase(str(db_path))

        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO results (
                    id, jan, asin, title, brand, price, amazon_price, profit,
                    margin, roi, rank, sellers, restriction, judgment,
                    amazon_url, keepa_url, ms_url, in_stock, is_favorite, is_checked
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "4939553041535_1",
                "4939553041535",
                "B07XSD7C2Q",
                "旧IDのテスト商品",
                "セザンヌ",
                660,
                1100,
                -50,
                "-4%",
                "-7%",
                "圏外",
                1,
                "出品可能",
                "❌ 利益なし",
                "https://www.amazon.co.jp/dp/B07XSD7C2Q",
                "https://keepa.com/#!product/5-B07XSD7C2Q",
                "https://www.make-up-solution.com/ec/pro/disp/1/4939553041535",
                1,
                1,
                1,
            ))
            conn.commit()

        saved = db.save_result(make_result(price=640, profit=10, roi="1%"))
        rows = db.get_all_results()

        assert saved["id"].startswith("url_")
        assert len(rows) == 1
        assert rows[0]["id"] == saved["id"]
        assert rows[0]["is_favorite"] == 1
        assert rows[0]["is_checked"] == 1


def test_clear_keeps_favorite_and_checked_rows():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = ResearchDatabase(str(Path(tmpdir) / "history.db"))

        favorite = db.save_result(make_result(
            jan="4900000000001",
            asin="B000000001",
            ms_url="https://example.com/item/favorite",
        ))
        checked = db.save_result(make_result(
            jan="4900000000002",
            asin="B000000002",
            ms_url="https://example.com/item/checked",
        ))
        plain = db.save_result(make_result(
            jan="4900000000003",
            asin="B000000003",
            ms_url="https://example.com/item/plain",
        ))

        db.update_result_status(favorite["id"], "favorite", True)
        db.update_result_status(checked["id"], "checked", True)

        db.clear_all_results()
        rows = {row["id"]: row for row in db.get_all_results(limit=20)}

        assert favorite["id"] in rows
        assert checked["id"] in rows
        assert plain["id"] not in rows


if __name__ == "__main__":
    test_status_survives_research_updates()
    test_legacy_row_status_is_migrated_to_stable_id()
    test_clear_keeps_favorite_and_checked_rows()
    print("OK")
