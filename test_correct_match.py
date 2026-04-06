from core.matcher import ProductMatcher

# ヨドバシ側のデータ
yodo_item = {
    "title": "明治 meiji ほほえみ 明治ほほえみ らくらくキューブ 1620g 赤ちゃん用 0ヶ月～1歳頃",
    "brand": "明治"
}

# Amazon側の候補（正しいカタログ）
amazon_candidates = [
    {
        "asin": "B0DV8ZBDQY",
        "title": "明治ほほえみ らくらくキューブ（特大箱）1620g（27g×60袋）× 2箱",
        "brand": "明治"
    }
]

print("--- 正解ルートの照合テスト開始 ---")
best = ProductMatcher.find_best_match(yodo_item, amazon_candidates)

if best and best["asin"] == "B0DV8ZBDQY":
    print(f"✅ テスト成功: 正しい商品（1620g）をしっかり『本物』と認定しました！ Score: {best.get('match_score')}")
else:
    print(f"❌ テスト失敗: 正しい商品（1620g）なのに不一致となってしまいました。")

