MAKEUP_CATEGORIES = {
    "skincare": ("スキンケア", "https://www.make-up-solution.com/ec/Facet?category_0=11020000000"),
    "hair": ("ヘア", "https://www.make-up-solution.com/ec/Facet?category_0=11030000000"),
    "body": ("ボディ", "https://www.make-up-solution.com/ec/Facet?category_0=11040000000"),
    "makeup": ("メイク", "https://www.make-up-solution.com/ec/Facet?category_0=11050000000"),
    "fragrance": ("フレグランス", "https://www.make-up-solution.com/ec/Facet?category_0=11060000000"),
    "hand_nail": ("ハンド・ネイル", "https://www.make-up-solution.com/ec/Facet?category_0=11070000000"),
    "goods": ("雑貨", "https://www.make-up-solution.com/ec/Facet?category_0=11080000000"),
    "oral": ("オーラル", "https://www.make-up-solution.com/ec/Facet?category_0=110A0000000"),
    "mens": ("メンズ", "https://www.make-up-solution.com/ec/Facet?category_0=110B0000000"),
}

YODOBASHI_CATEGORIES = {
    "outlet": ("アウトレット", "https://www.yodobashi.com/ec/category/index.html?word=%E3%82%A2%E3%82%A6%E3%83%88%E3%83%AC%E3%83%83%E3%83%88"),
    "home": ("家電・日用品", "https://www.yodobashi.com/category/170063/"),
    "appliances": ("生活家電", "https://www.yodobashi.com/category/6353/"),
    "pc": ("パソコン・周辺機器", "https://www.yodobashi.com/category/19531/"),
    "camera": ("カメラ・写真", "https://www.yodobashi.com/category/19055/"),
    "audio": ("オーディオ", "https://www.yodobashi.com/category/22052/500000073035/"),
    "pet": ("ペット用品・フード", "https://www.yodobashi.com/category/162842/166369/"),
    "kitchen": ("キッチン用品・食器", "https://www.yodobashi.com/category/162842/162843/"),
    "health": ("ヘルス＆ビューティー", "https://www.yodobashi.com/category/159888/"),
    "toys": ("おもちゃ・ホビー", "https://www.yodobashi.com/category/141001/141336/"),
    "food": ("食品・飲料・お酒", "https://www.yodobashi.com/category/157851/"),
}

NETSEA_CATEGORIES = {
    "makeup": ("メイク・コスメ", "https://www.netsea.jp/search/?category_id=302"),
    "skincare": ("スキンケア・基礎化粧品", "https://www.netsea.jp/search/?category_id=313"),
    "hair": ("ヘアケア", "https://www.netsea.jp/search/?category_id=315"),
    "body": ("ボディケア", "https://www.netsea.jp/search/?category_id=303"),
    "fragrance": ("香水", "https://www.netsea.jp/search/?category_id=306"),
    "tools": ("メイク道具", "https://www.netsea.jp/search/?category_id=314"),
    "nail": ("ネイルケア", "https://www.netsea.jp/search/?category_id=316"),
    "hygiene": ("衛生日用品", "https://www.netsea.jp/search/?category_id=305"),
    "beauty_health": ("美容・健康用品", "https://www.netsea.jp/search/?category_id=304"),
    "seasonal": ("季節用品", "https://www.netsea.jp/search/?category_id=317"),
    "aroma": ("ヒーリング・アロマグッズ", "https://www.netsea.jp/search/?category_id=318"),
}

KAUNET_CATEGORIES = {
    "daily_life": ("日用品・生活雑貨", "https://www.kaunet.com/rakuraku/category/0/1/004/"),
    "drink_food_gift": ("ドリンク・フード・ギフト", "https://www.kaunet.com/rakuraku/category/0/1/022/"),
    "stationery": ("文房具・事務用品", "https://www.kaunet.com/rakuraku/category/0/1/001/"),
    "files": ("ファイル", "https://www.kaunet.com/rakuraku/category/0/1/016/"),
    "paper_toner_ink": ("コピー用紙・トナー・インク", "https://www.kaunet.com/rakuraku/category/0/1/002/"),
    "pc_printer_media": ("パソコン用品・プリンタ・メディア", "https://www.kaunet.com/rakuraku/category/0/1/017/"),
    "electronics_office": ("電化製品・電化消耗品・照明・事務機器", "https://www.kaunet.com/rakuraku/category/0/1/019/"),
    "packing_store": ("梱包・物流・現場用品・店舗用品", "https://www.kaunet.com/rakuraku/category/0/1/023/"),
    "medical_care_lab": ("衛生・医療・介護・研究用品", "https://www.kaunet.com/rakuraku/category/0/1/028/"),
    "tools_parts": ("工具・計測用品・機械・電気電子部品", "https://www.kaunet.com/rakuraku/category/0/1/025/"),
}

SITE_CONFIGS = {
    "makeup": {
        "display_name": "MakeUp Solution",
        "placeholder": "https://www.make-up-solution.com/...",
        "default_categories": ["makeup", "skincare"],
        "categories": MAKEUP_CATEGORIES,
        "sort_options": [
            {"value": "disp_from_datetime", "label": "新着順"},
            {"value": "selling_price0_min", "label": "価格が安い順"},
            {"value": "selling_price0_max", "label": "価格が高い順"},
            {"value": "review", "label": "クチコミが多い順"},
        ],
    },
    "yodobashi": {
        "display_name": "ヨドバシ.com",
        "placeholder": "https://www.yodobashi.com/...",
        "default_categories": ["health"],
        "categories": YODOBASHI_CATEGORIES,
        "sort_options": [
            {"value": "new_arrival", "label": "新着順"},
            {"value": "price_asc", "label": "価格が安い順"},
            {"value": "price_desc", "label": "価格が高い順"},
            {"value": "score", "label": "人気順"},
        ],
    },
    "netsea": {
        "display_name": "NETSEA",
        "placeholder": "https://www.netsea.jp/...",
        "default_categories": ["makeup", "skincare"],
        "categories": NETSEA_CATEGORIES,
        "sort_options": [
            {"value": "new_arrival", "label": "新着順"},
            {"value": "price_asc", "label": "価格が安い順"},
            {"value": "price_desc", "label": "価格が高い順"},
        ],
    },
    "kaunet": {
        "display_name": "カウネット",
        "placeholder": "https://www.kaunet.com/...",
        "default_categories": ["stationery"],
        "categories": KAUNET_CATEGORIES,
        "sort_options": [
            {"value": "default", "label": "標準順"},
        ],
    },
}


def get_site_config(site_key):
    return SITE_CONFIGS.get(site_key, SITE_CONFIGS["makeup"])


def get_category_map(site_key):
    return get_site_config(site_key)["categories"]


def get_default_categories(site_key):
    return list(get_site_config(site_key)["default_categories"])


def get_category_url(site_key, category_key):
    return get_category_map(site_key).get(category_key, ("不明", ""))[1]


def serialize_site_configs():
    payload = {}
    for site_key, config in SITE_CONFIGS.items():
        payload[site_key] = {
            "display_name": config["display_name"],
            "placeholder": config["placeholder"],
            "default_categories": list(config["default_categories"]),
            "sort_options": list(config["sort_options"]),
            "categories": [
                {"value": key, "label": label}
                for key, (label, _url) in config["categories"].items()
            ],
        }
    return payload
