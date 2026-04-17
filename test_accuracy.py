import unittest

from core.matcher import ProductMatcher


class ProductMatcherFalsePositiveTests(unittest.TestCase):
    def test_different_package_size_is_rejected(self):
        yodo_item = {
            "title": "明治 meiji ほほえみ 明治ほほえみ らくらくキューブ 1620g 赤ちゃん用 0ヶ月～1歳頃",
            "brand": "明治",
        }
        amazon_candidates = [
            {
                "asin": "B01N6EL2MJ",
                "title": "明治 ほほえみ 800g ×2セット",
                "brand": "明治",
            }
        ]

        best = ProductMatcher.find_best_match(yodo_item, amazon_candidates)

        self.assertIsNone(best)


if __name__ == "__main__":
    unittest.main()
