"""
Keepa API Integration Module
- Token-aware rate limiting (max 60 tokens, 1 token/min recovery)
- 24-hour cache to avoid redundant API calls
- Provides: monthly sales (drops), price stability, seller trends
"""
import os
import time
import httpx
import json
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

KEEPA_API_URL = "https://api.keepa.com"
CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "keepa_cache.json")

class KeepaAPI:
    def __init__(self):
        self.api_key = os.environ.get("KEEPA_API_KEY", "")
        self.tokens_left = 60
        self.last_request_time = 0
        self._cache = self._load_cache()
    
    # ---- Cache Management ----
    def _load_cache(self):
        """Load cached Keepa results from disk."""
        try:
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                # Purge entries older than 24 hours
                cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
                return {k: v for k, v in cache.items() if v.get("cached_at", "") > cutoff}
            return {}
        except Exception as e:
            logger.warning(f"Failed to load Keepa cache: {e}")
            return {}
    
    def _save_cache(self):
        """Persist cache to disk."""
        try:
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save Keepa cache: {e}")
    
    def _get_cached(self, asin):
        """Get cached data if available and fresh (< 24h)."""
        if asin in self._cache:
            entry = self._cache[asin]
            cached_at = entry.get("cached_at", "")
            if cached_at:
                try:
                    cache_time = datetime.fromisoformat(cached_at)
                    if datetime.now() - cache_time < timedelta(hours=24):
                        logger.info(f"Keepa cache hit: {asin}")
                        return entry.get("data")
                except Exception:
                    pass
        return None
    
    def _set_cached(self, asin, data):
        """Store result in cache."""
        self._cache[asin] = {
            "cached_at": datetime.now().isoformat(),
            "data": data
        }
        self._save_cache()
    
    # ---- Token Management ----
    def _wait_for_token(self):
        """Ensure we have tokens available. Wait if necessary."""
        if self.tokens_left <= 5:
            wait_secs = max(0, 60 - (time.time() - self.last_request_time))
            if wait_secs > 0:
                logger.info(f"Keepa token low ({self.tokens_left}). Waiting {int(wait_secs)}s for recovery...")
                time.sleep(wait_secs)
    
    # ---- API Request ----
    async def _request(self, endpoint, params):
        """Make a Keepa API request with token tracking."""
        if not self.api_key:
            logger.warning("KEEPA_API_KEY not set. Skipping Keepa request.")
            return None
        
        self._wait_for_token()
        
        params["key"] = self.api_key
        url = f"{KEEPA_API_URL}/{endpoint}"
        
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url, params=params, timeout=30.0)
                if res.status_code == 200:
                    data = res.json()
                    self.tokens_left = data.get("tokensLeft", self.tokens_left)
                    self.last_request_time = time.time()
                    logger.info(f"Keepa API success. Tokens remaining: {self.tokens_left}")
                    return data
                elif res.status_code == 429:
                    logger.warning("Keepa rate limited (429). Will retry later.")
                    self.tokens_left = 0
                    return None
                else:
                    logger.error(f"Keepa API error {res.status_code}: {res.text[:200]}")
                    return None
        except Exception as e:
            logger.error(f"Keepa request error: {e}")
            return None
    
    # ---- High-level Methods ----
    async def get_product_data(self, asin):
        """
        Fetch comprehensive product data for an ASIN.
        Returns dict with: monthly_sales, avg_price, current_price, 
                          price_stability, seller_count_trend, sales_rank
        Uses cache to minimize token consumption.
        """
        if not asin or asin == "—":
            return self._empty_result()
        
        # Check cache first
        cached = self._get_cached(asin)
        if cached:
            return cached
        
        # Fetch from Keepa API
        # stats=1 includes statistics, days=90 for 3-month window
        params = {
            "domain": 5,  # Amazon.co.jp
            "asin": asin,
            "stats": 90,  # 90-day statistics
        }
        
        raw = await self._request("product", params)
        if not raw or "products" not in raw or not raw["products"]:
            result = self._empty_result()
            self._set_cached(asin, result)
            return result
        
        product = raw["products"][0]
        result = self._parse_product(product)
        self._set_cached(asin, result)
        return result
    
    async def search_by_ean(self, ean_code):
        """
        Search Keepa for a product by EAN/JAN code.
        Returns ASIN if found, None otherwise.
        Uses 1 token per request.
        """
        if not ean_code or not ean_code.strip().isdigit():
            return None
        
        params = {
            "domain": 5,  # Amazon.co.jp
            "type": "product",
            "ean": ean_code.strip(),
        }
        
        raw = await self._request("search", params)
        if raw and raw.get("asinList"):
            asin_list = raw["asinList"]
            if asin_list:
                logger.info(f"Keepa EAN search: {ean_code} -> ASIN: {asin_list[0]}")
                return asin_list[0]
        
        return None
    
    def _parse_product(self, product):
        """Parse Keepa product data into a clean dict."""
        stats = product.get("stats", {})
        
        # --- Monthly Sales (Drops in last 30 days) ---
        # "current" array index 0-17 maps to different price types
        # Sales rank drops = approximate sales count
        drops_30 = 0
        drops_90 = 0
        try:
            current = stats.get("current", [])
            # Index 4 = salesRankDrops30
            # Index 5 = salesRankDrops90
            if len(current) > 5:
                drops_30 = current[4] if current[4] and current[4] >= 0 else 0
                drops_90 = current[5] if current[5] and current[5] >= 0 else 0
        except Exception:
            pass
        
        # --- Average Price (Amazon price, 90 days) ---
        avg_price = 0
        current_price = 0
        try:
            avg_data = stats.get("avg", [])
            current_data = stats.get("current", [])
            # Index 0 = Amazon price, Index 1 = New 3rd party price
            if avg_data:
                # Amazon price avg (index 0), fallback to New price avg (index 1)
                if len(avg_data) > 0 and avg_data[0] and avg_data[0] > 0:
                    avg_price = avg_data[0]
                elif len(avg_data) > 1 and avg_data[1] and avg_data[1] > 0:
                    avg_price = avg_data[1]
            
            if current_data:
                if len(current_data) > 0 and current_data[0] and current_data[0] > 0:
                    current_price = current_data[0]
                elif len(current_data) > 1 and current_data[1] and current_data[1] > 0:
                    current_price = current_data[1]
        except Exception:
            pass
        
        # --- Price Stability ---
        # Compare current price vs 90-day average
        price_stability = "安定"
        if avg_price > 0 and current_price > 0:
            ratio = current_price / avg_price
            if ratio > 1.3:
                price_stability = "⚠️ 高騰中"
            elif ratio < 0.7:
                price_stability = "📉 下落中"
            else:
                price_stability = "✅ 安定"
        
        # --- Sales Rank ---
        sales_rank = -1
        try:
            current_data = stats.get("current", [])
            if len(current_data) > 3 and current_data[3] and current_data[3] > 0:
                sales_rank = current_data[3]
        except Exception:
            pass
        
        # --- Seller Count (New offers) ---
        new_offer_count = 0
        try:
            current_data = stats.get("current", [])
            if len(current_data) > 11 and current_data[11] and current_data[11] >= 0:
                new_offer_count = current_data[11]
        except Exception:
            pass
        
        # --- Monthly Sales Display ---
        if drops_30 > 0:
            if drops_30 >= 100:
                monthly_sales_str = f"激売れ✨ ({drops_30}回/月)"
            elif drops_30 >= 30:
                monthly_sales_str = f"高回転🔥 ({drops_30}回/月)"
            elif drops_30 >= 10:
                monthly_sales_str = f"普通 ({drops_30}回/月)"
            else:
                monthly_sales_str = f"低回転 ({drops_30}回/月)"
        elif drops_90 > 0:
            monthly_est = drops_90 // 3
            monthly_sales_str = f"約{monthly_est}回/月 (90日推計)"
        else:
            monthly_sales_str = "データ不足"
        
        return {
            "monthly_sales": monthly_sales_str,
            "drops_30": drops_30,
            "drops_90": drops_90,
            "avg_price_90": avg_price,
            "current_price": current_price,
            "price_stability": price_stability,
            "sales_rank": sales_rank,
            "new_offer_count": new_offer_count,
            "source": "keepa"
        }
    
    def _empty_result(self):
        """Return empty/default result dict."""
        return {
            "monthly_sales": "データなし",
            "drops_30": 0,
            "drops_90": 0,
            "avg_price_90": 0,
            "current_price": 0,
            "price_stability": "不明",
            "sales_rank": -1,
            "new_offer_count": 0,
            "source": "none"
        }
    
    def get_tokens_left(self):
        """Return remaining API tokens."""
        return self.tokens_left
