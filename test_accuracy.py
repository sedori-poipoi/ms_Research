from core.matcher import ProductMatcher

# ヨドバシ側のデータ
yodo_item = {
    "title": "明治 meiji ほほえみ 明治ほほえみ らくらくキューブ 1620g 赤ちゃん用 0ヶ月～1歳頃",
    "brand": "明治"
}

# Amazon側の候補（誤検知だったもの）
amazon_candidates = [
    {
        "asin": "B01N6EL2MJ",
        "title": "明治 ほほえみ 800g ×2セット",
        "brand": "明治"
    }
]

print("--- AI照合テスト開始 ---")
best = ProductMatcher.find_best_match(yodo_item, amazon_candidates)

if best is None:
    print("✅ テスト成功: 1620g と 800g が違う商品であることをAIが見破り、誤検知を回避しました。")
else:
    print(f"❌ テスト失敗: 商品を同一と判定してしまいました。 Score: {best.get('match_score')}")

