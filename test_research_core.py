import asyncio
import unittest
from unittest.mock import AsyncMock, patch
import tempfile
from pathlib import Path
import sqlite3

import app_main
from fastapi.testclient import TestClient
from core.database import ResearchDatabase
from core.keepa_api import KeepaAPI
from core.keepa_csv_import import load_keepa_csv_from_bytes
from core.site_config import SITE_CONFIGS, get_default_categories, serialize_site_configs
from core.kaunet_scraper import KaunetScraper
from core.skater_scraper import SkaterScraper
from core.yodobashi_scraper import YodobashiScraper


class SiteConfigTests(unittest.TestCase):
    def test_all_sites_have_default_categories(self):
        for site_key, config in SITE_CONFIGS.items():
            defaults = get_default_categories(site_key)
            self.assertTrue(defaults, f"{site_key} should have at least one default category")
            for category_key in defaults:
                self.assertIn(category_key, config["categories"])

    def test_serialized_site_configs_match_internal_definitions(self):
        serialized = serialize_site_configs()
        for site_key, config in SITE_CONFIGS.items():
            self.assertIn(site_key, serialized)
            self.assertEqual(serialized[site_key]["default_categories"], config["default_categories"])
            serialized_keys = [item["value"] for item in serialized[site_key]["categories"]]
            self.assertEqual(serialized_keys, list(config["categories"].keys()))

    def test_makeup_solution_includes_current_top_level_categories(self):
        makeup_categories = SITE_CONFIGS["makeup"]["categories"]
        self.assertIn("oral", makeup_categories)
        self.assertIn("mens", makeup_categories)
        self.assertEqual(makeup_categories["oral"][0], "オーラル")
        self.assertEqual(makeup_categories["mens"][0], "メンズ")

    def test_netsea_categories_follow_expected_site_order(self):
        expected_order = [
            "makeup",
            "skincare",
            "hair",
            "body",
            "fragrance",
            "tools",
            "nail",
            "hygiene",
            "beauty_health",
            "seasonal",
            "aroma",
        ]
        self.assertEqual(list(SITE_CONFIGS["netsea"]["categories"].keys()), expected_order)

    def test_kaunet_defaults_focus_on_low_risk_public_categories(self):
        kaunet_categories = SITE_CONFIGS["kaunet"]["categories"]
        expected_order = [
            "daily_life",
            "drink_food_gift",
            "stationery",
            "files",
            "paper_toner_ink",
            "pc_printer_media",
            "electronics_office",
            "packing_store",
            "medical_care_lab",
            "tools_parts",
        ]
        self.assertEqual(SITE_CONFIGS["kaunet"]["default_categories"], ["stationery"])
        self.assertEqual(list(kaunet_categories.keys()), expected_order)
        self.assertEqual(kaunet_categories["daily_life"][0], "日用品・生活雑貨")
        self.assertEqual(kaunet_categories["stationery"][0], "文房具・事務用品")

    def test_skater_defaults_focus_on_public_top_categories(self):
        skater_categories = SITE_CONFIGS["skater"]["categories"]
        expected_order = [
            "lunchbox",
            "bottle",
            "kitchen",
            "life",
        ]
        self.assertEqual(SITE_CONFIGS["skater"]["default_categories"], ["lunchbox"])
        self.assertEqual(list(skater_categories.keys()), expected_order)
        self.assertEqual(skater_categories["lunchbox"][0], "お弁当箱・ランチグッズ")
        self.assertEqual(skater_categories["bottle"][0], "水筒・タンブラー")


class KeepaApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_for_token_uses_async_sleep(self):
        api = KeepaAPI()
        api.tokens_left = 0
        api.last_request_time = 0

        with patch("core.keepa_api.asyncio.sleep", new=AsyncMock()) as mocked_sleep:
            with patch("core.keepa_api.time.time", return_value=0):
                await api._wait_for_token()

        mocked_sleep.assert_awaited_once_with(60)


class KeepaCsvImportTests(unittest.TestCase):
    def test_load_keepa_csv_indexes_by_ean(self):
        csv_data = (
            "ASIN,商品名,商品コード: EAN,ブランド,Buy Box: 現在価格,新品: 現在価格,Amazon: 現在価格,"
            "月間売上トレンド: 先月の購入,売れ筋ランキング: 現在価格,FBA Pick&Pack 料金,現在のBuy Box価格に基づく紹介料,紹介料％\n"
            "B000TEST01,テスト商品,4901234567890,テストブランド,\"2,980\",\"2,700\",\"3,100\",120,15,425,447,15%\n"
        ).encode("utf-8-sig")

        loaded = load_keepa_csv_from_bytes(csv_data, filename="sample.csv")

        self.assertEqual(loaded["meta"]["total_rows"], 1)
        self.assertEqual(loaded["meta"]["indexed_eans"], 1)
        self.assertIn("4901234567890", loaded["by_ean"])
        self.assertEqual(loaded["by_ean"]["4901234567890"]["asin"], "B000TEST01")
        self.assertEqual(loaded["by_ean"]["4901234567890"]["buy_box_price"], 2980)

    def test_keepa_csv_upload_accepts_raw_csv_body(self):
        client = TestClient(app_main.app)
        original_status = dict(app_main.session_data["keepa_csv"])
        original_store = {
            "by_ean": dict(app_main.keepa_csv_store["by_ean"]),
            "meta": dict(app_main.keepa_csv_store["meta"]),
        }
        original_cache_dir = app_main.KEEPA_CSV_CACHE_DIR
        original_cache_file = app_main.KEEPA_CSV_CACHE_FILE
        original_cache_meta = app_main.KEEPA_CSV_CACHE_META_FILE
        with tempfile.TemporaryDirectory() as tmpdir:
            app_main.KEEPA_CSV_CACHE_DIR = tmpdir
            app_main.KEEPA_CSV_CACHE_FILE = str(Path(tmpdir) / "latest_keepa.csv")
            app_main.KEEPA_CSV_CACHE_META_FILE = str(Path(tmpdir) / "latest_keepa_meta.json")
            try:
                response = client.post(
                    "/keepa-csv/upload",
                    content=(
                        "ASIN,商品名,商品コード: EAN,ブランド,Buy Box: 現在価格\n"
                        "B000TEST01,テスト商品,4901234567890,テストブランド,2980\n"
                    ).encode("utf-8-sig"),
                    headers={
                        "content-type": "text/csv",
                        "x-filename": "sample.csv",
                    },
                )
                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(body["status"], "success")
                self.assertEqual(body["indexed_eans"], 1)
                self.assertEqual(body["filename"], "sample.csv")
            finally:
                app_main.session_data["keepa_csv"] = original_status
                app_main.keepa_csv_store["by_ean"] = original_store["by_ean"]
                app_main.keepa_csv_store["meta"] = original_store["meta"]
                app_main.KEEPA_CSV_CACHE_DIR = original_cache_dir
                app_main.KEEPA_CSV_CACHE_FILE = original_cache_file
                app_main.KEEPA_CSV_CACHE_META_FILE = original_cache_meta

    def test_keepa_csv_cache_can_restore_after_restart(self):
        original_status = dict(app_main.session_data["keepa_csv"])
        original_store = {
            "by_ean": dict(app_main.keepa_csv_store["by_ean"]),
            "meta": dict(app_main.keepa_csv_store["meta"]),
        }
        original_cache_dir = app_main.KEEPA_CSV_CACHE_DIR
        original_cache_file = app_main.KEEPA_CSV_CACHE_FILE
        original_cache_meta = app_main.KEEPA_CSV_CACHE_META_FILE

        with tempfile.TemporaryDirectory() as tmpdir:
            app_main.KEEPA_CSV_CACHE_DIR = tmpdir
            app_main.KEEPA_CSV_CACHE_FILE = str(Path(tmpdir) / "latest_keepa.csv")
            app_main.KEEPA_CSV_CACHE_META_FILE = str(Path(tmpdir) / "latest_keepa_meta.json")
            csv_bytes = (
                "ASIN,商品名,商品コード: EAN,ブランド,Buy Box: 現在価格\n"
                "B000TEST01,テスト商品,4901234567890,テストブランド,2980\n"
            ).encode("utf-8-sig")
            loaded = load_keepa_csv_from_bytes(csv_bytes, filename="cached.csv")

            try:
                app_main.persist_keepa_csv_cache(csv_bytes, "cached.csv", loaded["meta"])
                app_main.keepa_csv_store["by_ean"] = {}
                app_main.keepa_csv_store["meta"] = {}
                app_main.session_data["keepa_csv"] = {
                    "loaded": False,
                    "filename": "",
                    "total_rows": 0,
                    "indexed_eans": 0,
                    "loaded_at": None,
                    "file_size_bytes": 0,
                }

                restored = app_main.restore_keepa_csv_cache()

                self.assertTrue(restored)
                self.assertTrue(app_main.session_data["keepa_csv"]["loaded"])
                self.assertEqual(app_main.session_data["keepa_csv"]["filename"], "cached.csv")
                self.assertIn("4901234567890", app_main.keepa_csv_store["by_ean"])
            finally:
                app_main.session_data["keepa_csv"] = original_status
                app_main.keepa_csv_store["by_ean"] = original_store["by_ean"]
                app_main.keepa_csv_store["meta"] = original_store["meta"]
                app_main.KEEPA_CSV_CACHE_DIR = original_cache_dir
                app_main.KEEPA_CSV_CACHE_FILE = original_cache_file
                app_main.KEEPA_CSV_CACHE_META_FILE = original_cache_meta


class RecommendationTests(unittest.TestCase):
    def test_generate_recommendations_includes_profit_and_watch(self):
        results = [
            {
                "title": "高利益商品",
                "profit": 850,
                "filter_status": "visible",
                "asin": "B000HIGH01",
                "monthly_sales": "12",
                "filter_reason": "",
            },
            {
                "title": "監視向き商品",
                "profit": 40,
                "filter_status": "visible",
                "asin": "B000WATCH1",
                "monthly_sales": "35",
                "filter_reason": "",
            },
            {
                "title": "低回転商品",
                "profit": -20,
                "filter_status": "filtered",
                "asin": "—",
                "monthly_sales": "データなし",
                "filter_reason": "Amazon未検出",
            },
        ]

        recommendations = app_main.generate_recommendations(results, {"テストブランド": 1200})

        self.assertEqual(len(recommendations), 3)
        self.assertEqual(recommendations[0]["type"], "restriction")
        self.assertTrue(any(rec["type"] == "profit" for rec in recommendations))
        self.assertTrue(any(rec["type"] == "watch" for rec in recommendations))

    def test_generate_recommendations_includes_csv_gap_hint(self):
        recommendations = app_main.generate_recommendations(
            [
                {
                    "title": "CSV一致商品",
                    "brand": "KAI",
                    "profit": 120,
                    "filter_status": "visible",
                    "asin": "B000MATCH01",
                    "monthly_sales": "25",
                    "filter_reason": "",
                },
                {
                    "title": "CSV未一致商品",
                    "brand": "セザンヌ",
                    "profit": 0,
                    "filter_status": "filtered",
                    "asin": "—",
                    "monthly_sales": "データなし",
                    "filter_reason": "CSV未一致",
                }
            ],
            {},
            match_mode="keepa_csv",
            keepa_csv_meta={"filename": "sample.csv", "total_rows": 500, "indexed_eans": 495},
        )

        self.assertTrue(any(rec["type"] == "csv_gap" for rec in recommendations))
        csv_gap = next(rec for rec in recommendations if rec["type"] == "csv_gap")
        self.assertIn("一致率 50.0%", csv_gap["message"])
        self.assertIn("495件がEAN付き", csv_gap["message"])
        self.assertIn("セザンヌ", csv_gap["message"])

    def test_generate_recommendations_falls_back_to_next_action(self):
        recommendations = app_main.generate_recommendations(
            [
                {
                    "title": "一致だけした商品",
                    "profit": -50,
                    "filter_status": "filtered",
                    "asin": "B000ONLY01",
                    "monthly_sales": "データなし",
                    "filter_reason": "Amazon未検出",
                }
            ],
            {},
        )

        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["type"], "next_action")

    def test_build_run_summary_and_site_report_include_new_dashboard_data(self):
        results = [
            {
                "title": "利益商品A",
                "asin": "B000A",
                "profit": 650,
                "roi": "18%",
                "filter_status": "visible",
                "filter_reason": "",
                "match_method": "jan_verified",
                "source_site_label": "スケーター公式",
                "restriction_code": "",
            },
            {
                "title": "監視商品B",
                "asin": "B000B",
                "profit": 40,
                "roi": "9%",
                "filter_status": "visible",
                "filter_reason": "",
                "match_method": "keepa_csv",
                "source_site_label": "スケーター公式",
                "restriction_code": "APPROVAL_REQUIRED",
            },
            {
                "title": "未一致商品C",
                "asin": "—",
                "profit": -30,
                "roi": "-2%",
                "filter_status": "filtered",
                "filter_reason": "CSV未一致",
                "match_method": "amazon_unmatched",
                "source_site_label": "カウネット",
                "restriction_code": "",
            },
        ]

        summary = app_main.build_run_summary(results, match_mode="keepa_csv")
        site_report = app_main.build_site_report(results)

        self.assertTrue(any(card["label"] == "CSV一致率" for card in summary["cards"]))
        self.assertTrue(any("JAN/CSV一致" in highlight for highlight in summary["highlights"]))
        self.assertEqual(site_report[0]["site_label"], "スケーター公式")
        self.assertEqual(site_report[0]["watch_count"], 1)


class YodobashiScraperTests(unittest.TestCase):
    def test_build_listing_url_for_single_level_category(self):
        scraper = YodobashiScraper(headless=True)
        url = scraper._build_listing_url(
            "https://www.yodobashi.com/category/159888/",
            page_num=2,
            sort_order="price_desc",
        )
        self.assertEqual(
            url,
            "https://www.yodobashi.com/?word=&cate=159888&sorttyp=price_desc&page=2",
        )

    def test_build_listing_url_preserves_deep_category_path(self):
        scraper = YodobashiScraper(headless=True)
        url = scraper._build_listing_url(
            "https://www.yodobashi.com/category/162842/162843/",
            page_num=3,
            sort_order="new_arrival",
        )
        self.assertEqual(
            url,
            "https://www.yodobashi.com/category/162842/162843/?page=3&sorttyp=new_arrival",
        )


class KaunetScraperTests(unittest.TestCase):
    def test_build_listing_url_updates_path_page_segment(self):
        scraper = KaunetScraper(headless=True)
        url = scraper._build_listing_url(
            "https://www.kaunet.com/rakuraku/category/0/1/001/001004/",
            page_num=3,
        )
        self.assertEqual(
            url,
            "https://www.kaunet.com/rakuraku/category/0/3/001/001004/",
        )

    def test_build_listing_url_leaves_goods_page_unchanged(self):
        scraper = KaunetScraper(headless=True)
        goods_url = "https://www.kaunet.com/kaunet/goods/36783137/"
        self.assertEqual(scraper._build_listing_url(goods_url, page_num=2), goods_url)


class SkaterScraperTests(unittest.TestCase):
    def test_build_listing_url_adds_page_query_for_category(self):
        scraper = SkaterScraper(headless=True)
        url = scraper._build_listing_url(
            "https://www.skater-onlineshop.com/view/category/lunchbox",
            page_num=3,
        )
        self.assertEqual(
            url,
            "https://www.skater-onlineshop.com/view/category/lunchbox?page=3",
        )

    def test_build_listing_url_keeps_item_url_unchanged(self):
        scraper = SkaterScraper(headless=True)
        item_url = "https://www.skater-onlineshop.com/view/item/000000016421"
        self.assertEqual(scraper._build_listing_url(item_url, page_num=2), item_url)


class ResearchProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_page_mode_expands_page_count_from_items_per_page(self):
        class FakeAmazon:
            async def search_by_jan(self, jan):
                return []

            async def search_by_keyword(self, query, brand):
                return []

            async def get_competitive_pricing(self, asin):
                return {"price": 0, "listing_price": 0, "shipping": 0, "seller_count": 0}

            async def get_fees_estimate(self, asin, buy_box):
                return 0

            async def get_listing_restrictions(self, asin):
                return {"status": "出品可能", "reason_code": "", "approval_url": ""}

        class FakeKeepa:
            def get_tokens_left(self):
                return 60

            async def get_product_data(self, asin):
                return {}

        class FakeScraper:
            instances = []

            def __init__(self, headless=False):
                self.calls = []
                type(self).instances.append(self)

            async def start(self):
                return None

            async def login(self):
                return "guest"

            async def get_stats(self, url):
                return {"total_items": 120, "items_per_page": 40}

            async def scrape_products(self, base_url, start_page, end_page, sort_order, max_items, skip_jans=None):
                self.calls.append({
                    "start_page": start_page,
                    "end_page": end_page,
                    "max_items": max_items,
                })
                if False:
                    yield None

            async def stop(self):
                return None

        with tempfile.TemporaryDirectory() as tmpdir:
            original_db = app_main.db
            original_session = dict(app_main.session_data)
            app_main.db = ResearchDatabase(str(Path(tmpdir) / "history.db"))
            app_main.session_data["results"] = []
            app_main.session_data["logs"] = []
            app_main.session_data["recommendations"] = []
            app_main.session_data["last_reset_time"] = 0
            FakeScraper.instances = []

            params = app_main.ResearchParams(
                target_site="makeup",
                categories=["makeup"],
                max_items=65,
                auto_page_mode=True,
                start_page=1,
                end_page=9,
            )

            try:
                with patch("app_main.AmazonSPAPI", FakeAmazon), \
                     patch("app_main.KeepaAPI", FakeKeepa), \
                     patch("core.scraper.MakeUpSolutionScraper", FakeScraper):
                    await app_main.run_research_task(params)

                self.assertEqual(len(FakeScraper.instances), 1)
                self.assertEqual(FakeScraper.instances[0].calls[0]["start_page"], 1)
                self.assertEqual(FakeScraper.instances[0].calls[0]["end_page"], 2)
                self.assertEqual(FakeScraper.instances[0].calls[0]["max_items"], 65)
            finally:
                app_main.db = original_db
                app_main.session_data.clear()
                app_main.session_data.update(original_session)

    async def test_keepa_csv_match_mode_uses_imported_ean_index(self):
        class FakeAmazon:
            async def search_by_jan(self, jan):
                raise AssertionError("CSV mode should not call Amazon JAN search")

            async def search_by_keyword(self, query, brand):
                raise AssertionError("CSV mode should not call Amazon keyword search")

            async def get_competitive_pricing(self, asin):
                raise AssertionError("CSV mode should not call Amazon pricing")

            async def get_fees_estimate(self, asin, buy_box):
                raise AssertionError("CSV mode should not call Amazon fees")

            async def get_listing_restrictions(self, asin):
                raise AssertionError("CSV mode should not call Amazon restrictions")

        class FakeKeepa:
            def get_tokens_left(self):
                return 60

            async def get_product_data(self, asin):
                raise AssertionError("CSV mode should not call Keepa API")

        class FakeScraper:
            def __init__(self, headless=False):
                self.headless = headless

            async def start(self):
                return None

            async def login(self):
                return "guest"

            async def get_stats(self, url):
                return {"total_items": 1, "items_per_page": 50}

            async def scrape_products(self, base_url, start_page, end_page, sort_order, max_items, skip_jans=None):
                yield {
                    "type": "item",
                    "data": {
                        "id": "4901234567890",
                        "jan": "4901234567890",
                        "title": "CSV照合用テスト商品",
                        "brand": "テストブランド",
                        "price": 1200,
                        "ms_url": "https://example.com/item/csv",
                        "page": 1,
                        "index": 1,
                        "points_rate": 0,
                        "in_stock": 1,
                    },
                }

            async def stop(self):
                return None

        class FakeHistory:
            def __init__(self):
                self.history = {}

            def add_to_history(self, jan):
                self.history[jan] = True

        with tempfile.TemporaryDirectory() as tmpdir:
            original_db = app_main.db
            original_history = app_main.history
            original_session = dict(app_main.session_data)
            original_csv_store = {
                "by_ean": dict(app_main.keepa_csv_store["by_ean"]),
                "meta": dict(app_main.keepa_csv_store["meta"]),
            }

            app_main.db = ResearchDatabase(str(Path(tmpdir) / "history.db"))
            app_main.history = FakeHistory()
            app_main.session_data["results"] = []
            app_main.session_data["logs"] = []
            app_main.session_data["recommendations"] = []
            app_main.session_data["last_reset_time"] = 0
            app_main.keepa_csv_store["by_ean"] = {
                "4901234567890": {
                    "asin": "B000TEST01",
                    "ean": "4901234567890",
                    "title": "CSV照合用テスト商品",
                    "brand": "テストブランド",
                    "buy_box_price": 2980,
                    "new_price": 2700,
                    "amazon_price": 3100,
                    "monthly_sales": 120,
                    "sales_rank": 15,
                    "seller_count": 4,
                    "fba_pick_pack_fee": 425,
                    "referral_fee": 447,
                    "referral_fee_rate": 15.0,
                    "root_category": "ホーム＆キッチン",
                    "sub_category": "テスト",
                    "amazon_url": "https://www.amazon.co.jp/dp/B000TEST01",
                    "keepa_url": "https://keepa.com/#!product/5-B000TEST01",
                }
            }
            app_main.keepa_csv_store["meta"] = {"filename": "sample.csv"}
            app_main.session_data["keepa_csv"] = {
                "loaded": True,
                "filename": "sample.csv",
                "total_rows": 1,
                "indexed_eans": 1,
                "loaded_at": "2026-04-18T10:00:00",
                "file_size_bytes": 1200,
            }

            app_main.db.save_result({
                "jan": "4901234567890",
                "asin": "B000TEST01",
                "title": "CSV照合用テスト商品",
                "brand": "テストブランド",
                "price": 1200,
                "amazon_price": 2500,
                "profit": 100,
                "margin": "8%",
                "roi": "8%",
                "rank": "20位",
                "sellers": 2,
                "restriction": "確認中",
                "judgment": "旧データ",
                "amazon_url": "https://www.amazon.co.jp/dp/B000TEST01",
                "keepa_url": "https://keepa.com/#!product/5-B000TEST01",
                "ms_url": "https://example.com/item/csv",
            })

            params = app_main.ResearchParams(
                target_site="makeup",
                categories=["makeup"],
                max_items=1,
                start_page=1,
                end_page=1,
                match_mode="keepa_csv",
            )

            try:
                with patch("app_main.AmazonSPAPI", FakeAmazon), \
                     patch("app_main.KeepaAPI", FakeKeepa), \
                     patch("core.scraper.MakeUpSolutionScraper", FakeScraper):
                    await app_main.run_research_task(params)

                result = app_main.session_data["results"][0]
                self.assertEqual(result["asin"], "B000TEST01")
                self.assertEqual(result["price"], 1200)
                self.assertEqual(result["amazon_price"], 2980)
                self.assertEqual(result["monthly_sales"], "120")
                self.assertEqual(result["restriction"], "CSV照合モード")
                self.assertEqual(result["match_method"], "keepa_csv")
                self.assertIn("Keepa CSV一致", result["match_label"])
                self.assertEqual(result["source_site_label"], "MakeUp Solution")
                self.assertEqual(result["profit_delta"], 808)
                self.assertIn("利益", result["change_summary"])
                self.assertTrue(app_main.session_data["run_summary"]["cards"])
                self.assertTrue(app_main.session_data["site_report"])
            finally:
                app_main.db = original_db
                app_main.history = original_history
                app_main.session_data.clear()
                app_main.session_data.update(original_session)
                app_main.keepa_csv_store["by_ean"] = original_csv_store["by_ean"]
                app_main.keepa_csv_store["meta"] = original_csv_store["meta"]

    async def test_full_category_mode_uses_total_item_count(self):
        class FakeAmazon:
            async def search_by_jan(self, jan):
                return []

            async def search_by_keyword(self, query, brand):
                return []

            async def get_competitive_pricing(self, asin):
                return {"price": 0, "listing_price": 0, "shipping": 0, "seller_count": 0}

            async def get_fees_estimate(self, asin, buy_box):
                return 0

            async def get_listing_restrictions(self, asin):
                return {"status": "出品可能", "reason_code": "", "approval_url": ""}

        class FakeKeepa:
            def get_tokens_left(self):
                return 60

            async def get_product_data(self, asin):
                return {}

        class FakeScraper:
            instances = []

            def __init__(self, headless=False):
                self.calls = []
                type(self).instances.append(self)

            async def start(self):
                return None

            async def login(self):
                return "guest"

            async def get_stats(self, url):
                return {"total_items": 120, "items_per_page": 40}

            async def scrape_products(self, base_url, start_page, end_page, sort_order, max_items, skip_jans=None):
                self.calls.append({
                    "start_page": start_page,
                    "end_page": end_page,
                    "max_items": max_items,
                })
                if False:
                    yield None

            async def stop(self):
                return None

        with tempfile.TemporaryDirectory() as tmpdir:
            original_db = app_main.db
            original_session = dict(app_main.session_data)
            app_main.db = ResearchDatabase(str(Path(tmpdir) / "history.db"))
            app_main.session_data["results"] = []
            app_main.session_data["logs"] = []
            app_main.session_data["recommendations"] = []
            app_main.session_data["last_reset_time"] = 0
            FakeScraper.instances = []

            params = app_main.ResearchParams(
                target_site="makeup",
                categories=["makeup"],
                max_items=25,
                auto_page_mode=True,
                full_category_mode=True,
            )

            try:
                with patch("app_main.AmazonSPAPI", FakeAmazon), \
                     patch("app_main.KeepaAPI", FakeKeepa), \
                     patch("core.scraper.MakeUpSolutionScraper", FakeScraper):
                    await app_main.run_research_task(params)

                self.assertEqual(len(FakeScraper.instances), 1)
                self.assertEqual(FakeScraper.instances[0].calls[0]["start_page"], 1)
                self.assertEqual(FakeScraper.instances[0].calls[0]["end_page"], 3)
                self.assertEqual(FakeScraper.instances[0].calls[0]["max_items"], 120)
            finally:
                app_main.db = original_db
                app_main.session_data.clear()
                app_main.session_data.update(original_session)

    async def test_all_sites_csv_mode_runs_multiple_sites_against_same_csv(self):
        class FakeAmazon:
            async def search_by_jan(self, jan):
                raise AssertionError("All-sites CSV mode should not call Amazon JAN search")

            async def search_by_keyword(self, query, brand):
                raise AssertionError("All-sites CSV mode should not call Amazon keyword search")

            async def get_competitive_pricing(self, asin):
                raise AssertionError("All-sites CSV mode should not call Amazon pricing")

            async def get_fees_estimate(self, asin, buy_box):
                raise AssertionError("All-sites CSV mode should not call Amazon fees")

            async def get_listing_restrictions(self, asin):
                raise AssertionError("All-sites CSV mode should not call Amazon restrictions")

        class FakeKeepa:
            def get_tokens_left(self):
                return 60

            async def get_product_data(self, asin):
                raise AssertionError("All-sites CSV mode should not call Keepa API")

        class FakeScraper:
            def __init__(self, site_key):
                self.site_key = site_key

            async def start(self):
                return None

            async def login(self):
                return "guest"

            async def get_stats(self, url):
                return {"total_items": 1, "items_per_page": 50}

            async def scrape_products(self, base_url, start_page, end_page, sort_order, max_items, skip_jans=None):
                site_map = {
                    "makeup": ("4901234567890", "MakeUp CSV商品"),
                    "skater": ("4973307723025", "Skater CSV商品"),
                }
                jan, title = site_map[self.site_key]
                yield {
                    "type": "item",
                    "data": {
                        "id": jan,
                        "jan": jan,
                        "title": title,
                        "brand": "テストブランド",
                        "price": 1200,
                        "ms_url": f"https://example.com/{self.site_key}/{jan}",
                        "points_rate": 0,
                        "in_stock": 1,
                    },
                }

            async def stop(self):
                return None

        class FakeHistory:
            def __init__(self):
                self.history = {}

            def add_to_history(self, jan):
                self.history[jan] = True

        with tempfile.TemporaryDirectory() as tmpdir:
            original_db = app_main.db
            original_history = app_main.history
            original_session = dict(app_main.session_data)
            original_csv_store = {
                "by_ean": dict(app_main.keepa_csv_store["by_ean"]),
                "meta": dict(app_main.keepa_csv_store["meta"]),
            }

            app_main.db = ResearchDatabase(str(Path(tmpdir) / "history.db"))
            app_main.history = FakeHistory()
            app_main.session_data["results"] = []
            app_main.session_data["logs"] = []
            app_main.session_data["recommendations"] = []
            app_main.session_data["last_reset_time"] = 0
            app_main.session_data["keepa_csv"] = {
                "loaded": True,
                "filename": "sample.csv",
                "total_rows": 2,
                "indexed_eans": 2,
                "loaded_at": "2026-04-18T11:20:00",
                "file_size_bytes": 2048,
            }
            app_main.keepa_csv_store["by_ean"] = {
                "4901234567890": {
                    "asin": "B000MAKE01",
                    "ean": "4901234567890",
                    "title": "MakeUp CSV商品",
                    "brand": "テストブランド",
                    "buy_box_price": 2980,
                    "new_price": 2700,
                    "amazon_price": 3100,
                    "monthly_sales": 120,
                    "sales_rank": 15,
                    "seller_count": 4,
                    "fba_pick_pack_fee": 425,
                    "referral_fee": 447,
                    "amazon_url": "https://www.amazon.co.jp/dp/B000MAKE01",
                    "keepa_url": "https://keepa.com/#!product/5-B000MAKE01",
                },
                "4973307723025": {
                    "asin": "B000SKATE1",
                    "ean": "4973307723025",
                    "title": "Skater CSV商品",
                    "brand": "テストブランド",
                    "buy_box_price": 3200,
                    "new_price": 3000,
                    "amazon_price": 3300,
                    "monthly_sales": 80,
                    "sales_rank": 21,
                    "seller_count": 3,
                    "fba_pick_pack_fee": 425,
                    "referral_fee": 480,
                    "amazon_url": "https://www.amazon.co.jp/dp/B000SKATE1",
                    "keepa_url": "https://keepa.com/#!product/5-B000SKATE1",
                },
            }
            app_main.keepa_csv_store["meta"] = {"filename": "sample.csv"}

            params = app_main.ResearchParams(
                target_site="makeup",
                target_sites=["makeup", "skater"],
                max_items=1,
                start_page=1,
                end_page=1,
                match_mode="all_sites_csv",
            )

            try:
                with patch("app_main.AmazonSPAPI", FakeAmazon), \
                     patch("app_main.KeepaAPI", FakeKeepa), \
                     patch("app_main._instantiate_scraper", side_effect=lambda site_key, monitor_mode=False: FakeScraper(site_key)):
                    await app_main.run_research_task(params)

                self.assertEqual(len(app_main.session_data["results"]), 2)
                sites = {row["source_site"] for row in app_main.session_data["results"]}
                self.assertEqual(sites, {"makeup", "skater"})
                self.assertTrue(all(row["match_method"] == "keepa_csv" for row in app_main.session_data["results"]))
                self.assertEqual(len(app_main.session_data["site_report"]), 2)
            finally:
                app_main.db = original_db
                app_main.history = original_history
                app_main.session_data.clear()
                app_main.session_data.update(original_session)
                app_main.keepa_csv_store["by_ean"] = original_csv_store["by_ean"]
                app_main.keepa_csv_store["meta"] = original_csv_store["meta"]

    async def test_multi_category_progress_counts_total_processed_items(self):
        class FakeAmazon:
            async def search_by_jan(self, jan):
                return [{"asin": f"B{jan[-9:]}", "brand": "テストブランド", "sales_rank": "1位"}]

            async def search_by_keyword(self, query, brand):
                return []

            async def get_competitive_pricing(self, asin):
                return {"price": 2400, "listing_price": 2400, "shipping": 0, "seller_count": 1}

            async def get_fees_estimate(self, asin, buy_box):
                return 500

            async def get_listing_restrictions(self, asin):
                return {"status": "出品可能", "reason_code": "", "approval_url": ""}

        class FakeKeepa:
            def get_tokens_left(self):
                return 60

            async def get_product_data(self, asin):
                return {
                    "source": "keepa",
                    "monthly_sales": "普通 (5回/月)",
                    "drops_30": 5,
                    "price_stability": "安定",
                    "new_offer_count": 2,
                }

        class FakeScraper:
            def __init__(self, headless=False):
                self.headless = headless

            async def start(self):
                return None

            async def login(self):
                return "guest"

            async def get_stats(self, url):
                return {"total_items": 100, "items_per_page": 50}

            async def scrape_products(self, base_url, start_page, end_page, sort_order, max_items, skip_jans=None):
                category_name = "makeup" if "11050000000" in base_url else "skincare"
                for idx in range(max_items):
                    jan = f"49000000000{idx}{1 if category_name == 'makeup' else 2}"
                    yield {
                        "type": "item",
                        "data": {
                            "id": jan,
                            "jan": jan,
                            "title": f"{category_name} 商品 {idx + 1}",
                            "brand": "テストブランド",
                            "price": 1000,
                            "ms_url": f"https://example.com/{category_name}/{idx + 1}",
                            "page": 1,
                            "index": idx + 1,
                            "points_rate": 0,
                            "in_stock": 1,
                        },
                    }

            async def stop(self):
                return None

        class FakeHistory:
            def __init__(self):
                self.history = {}

            def add_to_history(self, jan):
                self.history[jan] = True

        with tempfile.TemporaryDirectory() as tmpdir:
            original_db = app_main.db
            original_history = app_main.history
            original_session = dict(app_main.session_data)
            app_main.db = ResearchDatabase(str(Path(tmpdir) / "history.db"))
            app_main.history = FakeHistory()
            app_main.session_data["results"] = []
            app_main.session_data["logs"] = []
            app_main.session_data["recommendations"] = []
            app_main.session_data["progress"] = 0
            app_main.session_data["items_processed"] = 0
            app_main.session_data["total_items"] = 0
            app_main.session_data["last_reset_time"] = 0

            params = app_main.ResearchParams(
                target_site="makeup",
                categories=["makeup", "skincare"],
                max_items=2,
                start_page=1,
                end_page=1,
            )

            try:
                with patch("app_main.AmazonSPAPI", FakeAmazon), \
                     patch("app_main.KeepaAPI", FakeKeepa), \
                     patch("core.scraper.MakeUpSolutionScraper", FakeScraper):
                    await app_main.run_research_task(params)

                self.assertEqual(app_main.session_data["total_items"], 4)
                self.assertEqual(app_main.session_data["items_processed"], 4)
                self.assertEqual(app_main.session_data["progress"], 100)
                self.assertEqual(len(app_main.session_data["results"]), 4)
            finally:
                app_main.db = original_db
                app_main.history = original_history
                app_main.session_data.clear()
                app_main.session_data.update(original_session)

    async def test_recheck_no_brand_results_updates_existing_rows(self):
        class FakeAmazon:
            async def get_catalog_summary(self, asin):
                if asin == "B000000001":
                    return {"asin": asin, "brand": "花王", "title": "花王 テスト商品"}
                return {"asin": asin, "brand": "Generic", "title": "Generic ノーブランド商品"}

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "history.db"
            original_db = app_main.db
            original_session = dict(app_main.session_data)
            app_main.db = ResearchDatabase(str(db_path))
            app_main.session_data["results"] = []

            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO results (
                        id, jan, asin, title, brand, price, amazon_price, profit,
                        margin, roi, rank, sellers, restriction, judgment,
                        amazon_url, keepa_url, ms_url, in_stock, is_favorite, is_checked,
                        filter_status, filter_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    "item_keep",
                    "4900000000001",
                    "B000000001",
                    "既存商品",
                    "不明",
                    1000,
                    2000,
                    500,
                    "25%",
                    "50%",
                    "圏外",
                    1,
                    "出品可能",
                    "✅ 準・利益",
                    "#",
                    "#",
                    "https://example.com/item/keep",
                    1,
                    0,
                    0,
                    "visible",
                    "",
                ))
                cursor.execute("""
                    INSERT INTO results (
                        id, jan, asin, title, brand, price, amazon_price, profit,
                        margin, roi, rank, sellers, restriction, judgment,
                        amazon_url, keepa_url, ms_url, in_stock, is_favorite, is_checked,
                        filter_status, filter_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    "item_filter",
                    "4900000000002",
                    "B000000002",
                    "既存ノーブランド商品",
                    "不明",
                    1000,
                    2000,
                    500,
                    "25%",
                    "50%",
                    "圏外",
                    1,
                    "出品可能",
                    "✅ 準・利益",
                    "#",
                    "#",
                    "https://example.com/item/filter",
                    1,
                    0,
                    0,
                    "visible",
                    "",
                ))
                conn.commit()

            try:
                with patch("app_main.AmazonSPAPI", FakeAmazon):
                    summary = await app_main.recheck_no_brand_results(limit=20)

                self.assertEqual(summary["checked"], 2)
                self.assertEqual(summary["updated"], 2)

                rows = {row["id"]: row for row in app_main.db.get_all_results(limit=20)}
                self.assertEqual(rows["item_keep"]["brand"], "花王")
                self.assertEqual(rows["item_keep"]["filter_status"], "visible")
                self.assertEqual(rows["item_filter"]["filter_status"], "filtered")
                self.assertEqual(rows["item_filter"]["filter_reason"], "ノーブランド品")
            finally:
                app_main.db = original_db
                app_main.session_data.clear()
                app_main.session_data.update(original_session)

    async def test_unknown_source_brand_uses_amazon_brand_and_stays_visible(self):
        class FakeAmazon:
            async def search_by_jan(self, jan):
                return [{"asin": "B000000001", "brand": "花王", "sales_rank": "1位"}]

            async def search_by_keyword(self, query, brand):
                return []

            async def get_competitive_pricing(self, asin):
                return {"price": 2200, "listing_price": 2200, "shipping": 0, "seller_count": 1}

            async def get_fees_estimate(self, asin, buy_box):
                return 500

            async def get_listing_restrictions(self, asin):
                return {"status": "出品可能", "reason_code": "", "approval_url": ""}

        class FakeKeepa:
            def get_tokens_left(self):
                return 60

            async def get_product_data(self, asin):
                return {
                    "source": "keepa",
                    "monthly_sales": "普通 (5回/月)",
                    "drops_30": 5,
                    "price_stability": "安定",
                    "new_offer_count": 2,
                }

        class FakeScraper:
            def __init__(self, headless=False):
                self.headless = headless

            async def start(self):
                return None

            async def login(self):
                return "guest"

            async def get_stats(self, url):
                return {"total_items": 1, "items_per_page": 50}

            async def scrape_products(self, base_url, start_page, end_page, sort_order, max_items, skip_jans=None):
                yield {
                    "type": "item",
                    "data": {
                        "id": "4900000000001",
                        "jan": "4900000000001",
                        "title": "テスト商品",
                        "brand": "不明",
                        "price": 1000,
                        "ms_url": "https://example.com/item/1",
                        "page": 1,
                        "index": 1,
                        "points_rate": 0,
                        "in_stock": 1,
                    },
                }

            async def stop(self):
                return None

        class FakeHistory:
            def __init__(self):
                self.history = {}

            def add_to_history(self, jan):
                self.history[jan] = True

        with tempfile.TemporaryDirectory() as tmpdir:
            original_db = app_main.db
            original_history = app_main.history
            original_session = dict(app_main.session_data)
            app_main.db = ResearchDatabase(str(Path(tmpdir) / "history.db"))
            app_main.history = FakeHistory()
            app_main.session_data["results"] = []
            app_main.session_data["logs"] = []
            app_main.session_data["recommendations"] = []
            app_main.session_data["last_reset_time"] = 0

            params = app_main.ResearchParams(target_site="makeup", categories=["makeup"], max_items=1, start_page=1, end_page=1)

            try:
                with patch("app_main.AmazonSPAPI", FakeAmazon), \
                     patch("app_main.KeepaAPI", FakeKeepa), \
                     patch("core.scraper.MakeUpSolutionScraper", FakeScraper):
                    await app_main.run_research_task(params)

                result = app_main.session_data["results"][0]
                self.assertEqual(result["brand"], "花王")
                self.assertEqual(result["filter_status"], "visible")
            finally:
                app_main.db = original_db
                app_main.history = original_history
                app_main.session_data.clear()
                app_main.session_data.update(original_session)

    async def test_explicit_no_brand_items_are_filtered(self):
        class FakeAmazon:
            async def search_by_jan(self, jan):
                return [{"asin": "B000000002", "brand": "Generic", "sales_rank": "1位"}]

            async def search_by_keyword(self, query, brand):
                return []

            async def get_competitive_pricing(self, asin):
                return {"price": 2200, "listing_price": 2200, "shipping": 0, "seller_count": 1}

            async def get_fees_estimate(self, asin, buy_box):
                return 500

            async def get_listing_restrictions(self, asin):
                return {"status": "出品可能", "reason_code": "", "approval_url": ""}

        class FakeKeepa:
            def get_tokens_left(self):
                return 60

            async def get_product_data(self, asin):
                return {
                    "source": "keepa",
                    "monthly_sales": "普通 (5回/月)",
                    "drops_30": 5,
                    "price_stability": "安定",
                    "new_offer_count": 2,
                }

        class FakeScraper:
            def __init__(self, headless=False):
                self.headless = headless

            async def start(self):
                return None

            async def login(self):
                return "guest"

            async def get_stats(self, url):
                return {"total_items": 1, "items_per_page": 50}

            async def scrape_products(self, base_url, start_page, end_page, sort_order, max_items, skip_jans=None):
                yield {
                    "type": "item",
                    "data": {
                        "id": "4900000000002",
                        "jan": "4900000000002",
                        "title": "ノーブランド商品",
                        "brand": "不明",
                        "price": 1000,
                        "ms_url": "https://example.com/item/2",
                        "page": 1,
                        "index": 1,
                        "points_rate": 0,
                        "in_stock": 1,
                    },
                }

            async def stop(self):
                return None

        class FakeHistory:
            def __init__(self):
                self.history = {}

            def add_to_history(self, jan):
                self.history[jan] = True

        with tempfile.TemporaryDirectory() as tmpdir:
            original_db = app_main.db
            original_history = app_main.history
            original_session = dict(app_main.session_data)
            app_main.db = ResearchDatabase(str(Path(tmpdir) / "history.db"))
            app_main.history = FakeHistory()
            app_main.session_data["results"] = []
            app_main.session_data["logs"] = []
            app_main.session_data["recommendations"] = []
            app_main.session_data["last_reset_time"] = 0

            params = app_main.ResearchParams(target_site="makeup", categories=["makeup"], max_items=1, start_page=1, end_page=1)

            try:
                with patch("app_main.AmazonSPAPI", FakeAmazon), \
                     patch("app_main.KeepaAPI", FakeKeepa), \
                     patch("core.scraper.MakeUpSolutionScraper", FakeScraper):
                    await app_main.run_research_task(params)

                result = app_main.session_data["results"][0]
                self.assertEqual(result["filter_status"], "filtered")
                self.assertEqual(result["filter_reason"], "ノーブランド品")
            finally:
                app_main.db = original_db
                app_main.history = original_history
                app_main.session_data.clear()
                app_main.session_data.update(original_session)

    async def test_kaunet_research_uses_monitor_mode_for_visible_browser(self):
        class FakeAmazon:
            async def search_by_jan(self, jan):
                return [{"asin": "B000000003", "brand": "カウネット", "sales_rank": "1位"}]

            async def search_by_keyword(self, query, brand):
                return []

            async def get_competitive_pricing(self, asin):
                return {"price": 2200, "listing_price": 2200, "shipping": 0, "seller_count": 1}

            async def get_fees_estimate(self, asin, buy_box):
                return 500

            async def get_listing_restrictions(self, asin):
                return {"status": "出品可能", "reason_code": "", "approval_url": ""}

        class FakeKeepa:
            def get_tokens_left(self):
                return 60

            async def get_product_data(self, asin):
                return {
                    "source": "keepa",
                    "monthly_sales": "普通 (5回/月)",
                    "drops_30": 5,
                    "price_stability": "安定",
                    "new_offer_count": 2,
                }

        class FakeKaunetScraper:
            instances = []

            def __init__(self, headless=False):
                self.headless = headless
                type(self).instances.append(self)

            async def start(self):
                return None

            async def login(self):
                return "guest"

            async def get_stats(self, url):
                return {"total_items": 1, "items_per_page": 1}

            async def scrape_products(self, base_url, start_page, end_page, sort_order, max_items, skip_jans=None, full_category_mode=False):
                yield {
                    "type": "item",
                    "data": {
                        "id": "4900000000003",
                        "jan": "4900000000003",
                        "title": "カウネット商品",
                        "brand": "カウネット",
                        "price": 1000,
                        "ms_url": "https://www.kaunet.com/kaunet/goods/36783137/",
                        "page": 1,
                        "index": 1,
                        "points_rate": 0,
                        "in_stock": 1,
                    },
                }

            async def stop(self):
                return None

        class FakeHistory:
            def __init__(self):
                self.history = {}

            def add_to_history(self, jan):
                self.history[jan] = True

        with tempfile.TemporaryDirectory() as tmpdir:
            original_db = app_main.db
            original_history = app_main.history
            original_session = dict(app_main.session_data)
            app_main.db = ResearchDatabase(str(Path(tmpdir) / "history.db"))
            app_main.history = FakeHistory()
            app_main.session_data["results"] = []
            app_main.session_data["logs"] = []
            app_main.session_data["recommendations"] = []
            app_main.session_data["last_reset_time"] = 0
            FakeKaunetScraper.instances = []

            params = app_main.ResearchParams(
                target_site="kaunet",
                categories=["stationery"],
                max_items=1,
                start_page=1,
                end_page=1,
                monitor_mode=True,
            )

            try:
                with patch("app_main.AmazonSPAPI", FakeAmazon), \
                     patch("app_main.KeepaAPI", FakeKeepa), \
                     patch("core.kaunet_scraper.KaunetScraper", FakeKaunetScraper):
                    await app_main.run_research_task(params)

                self.assertEqual(len(FakeKaunetScraper.instances), 1)
                self.assertFalse(FakeKaunetScraper.instances[0].headless)
                self.assertEqual(app_main.session_data["results"][0]["brand"], "カウネット")
            finally:
                app_main.db = original_db
                app_main.history = original_history
                app_main.session_data.clear()
                app_main.session_data.update(original_session)

    async def test_skater_research_uses_monitor_mode_for_visible_browser(self):
        class FakeAmazon:
            async def search_by_jan(self, jan):
                return [{"asin": "B000000004", "brand": "スケーター", "sales_rank": "1位"}]

            async def search_by_keyword(self, query, brand):
                return []

            async def get_competitive_pricing(self, asin):
                return {"price": 2300, "listing_price": 2300, "shipping": 0, "seller_count": 1}

            async def get_fees_estimate(self, asin, buy_box):
                return 500

            async def get_listing_restrictions(self, asin):
                return {"status": "出品可能", "reason_code": "", "approval_url": ""}

        class FakeKeepa:
            def get_tokens_left(self):
                return 60

            async def get_product_data(self, asin):
                return {
                    "source": "keepa",
                    "monthly_sales": "普通 (5回/月)",
                    "drops_30": 5,
                    "price_stability": "安定",
                    "new_offer_count": 2,
                }

        class FakeSkaterScraper:
            instances = []

            def __init__(self, headless=False):
                self.headless = headless
                type(self).instances.append(self)

            async def start(self):
                return None

            async def login(self):
                return "guest"

            async def get_stats(self, url):
                return {"total_items": 0, "items_per_page": 24}

            async def scrape_products(self, base_url, start_page, end_page, sort_order, max_items, skip_jans=None):
                yield {
                    "type": "item",
                    "data": {
                        "id": "4973307723025",
                        "jan": "4973307723025",
                        "title": "スケーター アルミ弁当箱",
                        "brand": "スケーター",
                        "price": 2447,
                        "ms_url": "https://www.skater-onlineshop.com/view/item/000000016421",
                        "page": 1,
                        "index": 1,
                        "points_rate": 0.01,
                        "in_stock": 1,
                    },
                }

            async def stop(self):
                return None

        class FakeHistory:
            def __init__(self):
                self.history = {}

            def add_to_history(self, jan):
                self.history[jan] = True

        with tempfile.TemporaryDirectory() as tmpdir:
            original_db = app_main.db
            original_history = app_main.history
            original_session = dict(app_main.session_data)
            app_main.db = ResearchDatabase(str(Path(tmpdir) / "history.db"))
            app_main.history = FakeHistory()
            app_main.session_data["results"] = []
            app_main.session_data["logs"] = []
            app_main.session_data["recommendations"] = []
            app_main.session_data["last_reset_time"] = 0
            FakeSkaterScraper.instances = []

            params = app_main.ResearchParams(
                target_site="skater",
                categories=["lunchbox"],
                max_items=1,
                start_page=1,
                end_page=1,
                monitor_mode=True,
            )

            try:
                with patch("app_main.AmazonSPAPI", FakeAmazon), \
                     patch("app_main.KeepaAPI", FakeKeepa), \
                     patch("core.skater_scraper.SkaterScraper", FakeSkaterScraper):
                    await app_main.run_research_task(params)

                self.assertEqual(len(FakeSkaterScraper.instances), 1)
                self.assertFalse(FakeSkaterScraper.instances[0].headless)
                self.assertEqual(app_main.session_data["results"][0]["brand"], "スケーター")
            finally:
                app_main.db = original_db
                app_main.history = original_history
                app_main.session_data.clear()
                app_main.session_data.update(original_session)

    async def test_skater_research_rejects_mismatched_jan_candidate_without_keyword_fallback(self):
        class FakeAmazon:
            keyword_calls = 0

            async def search_by_jan(self, jan):
                return [{
                    "asin": "B01M1550NM",
                    "brand": "スケーター",
                    "title": "スケーター 4点ロック 弁当箱 900ml 大容量 ランチボックス 1段 ブルックリン 男性用 日本製 YZFL9",
                    "sales_rank": "1位",
                }]

            async def search_by_keyword(self, query, brand):
                type(self).keyword_calls += 1
                return [{
                    "asin": "B01M1550NM",
                    "brand": "スケーター",
                    "title": "スケーター 4点ロック 弁当箱 900ml 大容量 ランチボックス 1段 ブルックリン 男性用 日本製 YZFL9",
                    "sales_rank": "1位",
                }]

            async def get_competitive_pricing(self, asin):
                return {"price": 973, "listing_price": 973, "shipping": 0, "seller_count": 1}

            async def get_fees_estimate(self, asin, buy_box):
                return 500

            async def get_listing_restrictions(self, asin):
                return {"status": "出品可能", "reason_code": "", "approval_url": ""}

        class FakeKeepa:
            def get_tokens_left(self):
                return 60

            async def get_product_data(self, asin):
                return {}

        class FakeSkaterScraper:
            def __init__(self, headless=False):
                self.headless = headless

            async def start(self):
                return None

            async def login(self):
                return "guest"

            async def get_stats(self, url):
                return {"total_items": 1, "items_per_page": 1}

            async def scrape_products(self, base_url, start_page, end_page, sort_order, max_items, skip_jans=None):
                yield {
                    "type": "item",
                    "data": {
                        "id": "4973307596445",
                        "jan": "4973307596445",
                        "title": "木蓋付き ステンレス 弁当箱 1030ml ランチ ボックス パッキン 付き STLBT11B スケーター skater 木目 おしゃれ シンプル",
                        "brand": "スケーター",
                        "price": 5500,
                        "ms_url": "https://www.skater-onlineshop.com/view/item/000000009077",
                        "page": 1,
                        "index": 1,
                        "points_rate": 0.01,
                        "in_stock": 1,
                    },
                }

            async def stop(self):
                return None

        class FakeHistory:
            def __init__(self):
                self.history = {}

            def add_to_history(self, jan):
                self.history[jan] = True

        with tempfile.TemporaryDirectory() as tmpdir:
            original_db = app_main.db
            original_history = app_main.history
            original_session = dict(app_main.session_data)
            app_main.db = ResearchDatabase(str(Path(tmpdir) / "history.db"))
            app_main.history = FakeHistory()
            app_main.session_data["results"] = []
            app_main.session_data["logs"] = []
            app_main.session_data["recommendations"] = []
            app_main.session_data["last_reset_time"] = 0
            FakeAmazon.keyword_calls = 0

            params = app_main.ResearchParams(
                target_site="skater",
                categories=["lunchbox"],
                max_items=1,
                start_page=1,
                end_page=1,
            )

            try:
                with patch("app_main.AmazonSPAPI", FakeAmazon), \
                     patch("app_main.KeepaAPI", FakeKeepa), \
                     patch("core.skater_scraper.SkaterScraper", FakeSkaterScraper):
                    await app_main.run_research_task(params)

                result = app_main.session_data["results"][0]
                self.assertEqual(result["asin"], "—")
                self.assertEqual(result["amazon_price"], 0)
                self.assertEqual(result["filter_reason"], "Amazon未検出")
                self.assertEqual(FakeAmazon.keyword_calls, 0)
            finally:
                app_main.db = original_db
                app_main.history = original_history
                app_main.session_data.clear()
                app_main.session_data.update(original_session)


if __name__ == "__main__":
    unittest.main()
