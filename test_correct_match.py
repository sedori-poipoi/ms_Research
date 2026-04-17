import unittest

from core.matcher import ProductMatcher


class ProductMatcherCorrectMatchTests(unittest.TestCase):
    def test_matching_size_and_brand_is_accepted(self):
        yodo_item = {
            "title": "明治 meiji ほほえみ 明治ほほえみ らくらくキューブ 1620g 赤ちゃん用 0ヶ月～1歳頃",
            "brand": "明治",
        }
        amazon_candidates = [
            {
                "asin": "B0DV8ZBDQY",
                "title": "明治ほほえみ らくらくキューブ（特大箱）1620g（27g×60袋）× 2箱",
                "brand": "明治",
            }
        ]

        best = ProductMatcher.find_best_match(yodo_item, amazon_candidates)

        self.assertIsNotNone(best)
        self.assertEqual(best["asin"], "B0DV8ZBDQY")
        self.assertGreaterEqual(best.get("match_score", 0), 70)


if __name__ == "__main__":
    unittest.main()
