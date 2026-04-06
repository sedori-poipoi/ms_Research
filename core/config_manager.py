import json
import os
import logging

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
BRAND_FILE = os.path.join(CONFIG_PATH, "cleared_brands.json")

# Default brands provided by Nagi-san
DEFAULT_BRANDS = [
    "naris", "naris up", "いなば", "いなばペットフード", "いなばペットフード株式会社", 
    "いなば食品", "エクセル(excel)", "エプソン", "エレコム(elecom)", "キャンメイク", 
    "コンビ(combi)", "スケーター(skater)", "パナソニック(panasonic)", "ベストコ", 
    "協和紙工", "花王(kao)", "ペティオ (Petio)"
]

class ConfigManager:
    @staticmethod
    def ensure_config_dir():
        if not os.path.exists(CONFIG_PATH):
            os.makedirs(CONFIG_PATH, exist_ok=True)

    @staticmethod
    def load_brands():
        ConfigManager.ensure_config_dir()
        if not os.path.exists(BRAND_FILE):
            # Save defaults if no file exists
            ConfigManager.save_brands(DEFAULT_BRANDS)
            return DEFAULT_BRANDS
        
        try:
            with open(BRAND_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading brands: {e}")
            return DEFAULT_BRANDS

    @staticmethod
    def save_brands(brands):
        ConfigManager.ensure_config_dir()
        try:
            with open(BRAND_FILE, 'w', encoding='utf-8') as f:
                json.dump(brands, f, ensure_ascii=False, indent=4)
            return True
        except Exception as e:
            logger.error(f"Error saving brands: {e}")
            return False

    @staticmethod
    def add_brand(brand_name):
        brands = ConfigManager.load_brands()
        if brand_name not in brands:
            brands.append(brand_name)
            return ConfigManager.save_brands(brands)
        return True

    @staticmethod
    def remove_brand(brand_name):
        brands = ConfigManager.load_brands()
        if brand_name in brands:
            brands.remove(brand_name)
            return ConfigManager.save_brands(brands)
        return True
