import asyncio
import unittest
from unittest.mock import AsyncMock, patch
import tempfile
from pathlib import Path
import sqlite3

import app_main
from core.database import ResearchDatabase
from core.keepa_api import KeepaAPI
from core.site_config import SITE_CONFIGS, get_default_categories, serialize_site_configs
from core.kaunet_scraper import KaunetScraper
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


class KeepaApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_for_token_uses_async_sleep(self):
        api = KeepaAPI()
        api.tokens_left = 0
        api.last_request_time = 0

        with patch("core.keepa_api.asyncio.sleep", new=AsyncMock()) as mocked_sleep:
            with patch("core.keepa_api.time.time", return_value=0):
                await api._wait_for_token()

        mocked_sleep.assert_awaited_once_with(60)


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


if __name__ == "__main__":
    unittest.main()
