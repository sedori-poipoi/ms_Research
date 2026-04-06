import os
import time
import httpx
import asyncio
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SP_API_URL = "https://sellingpartnerapi-fe.amazon.com"
LWA_ENDPOINT = "https://api.amazon.co.jp/auth/o2/token"

class AmazonSPAPI:
    def __init__(self):
        self.client_id = os.environ.get("LWA_APP_ID")
        self.client_secret = os.environ.get("LWA_CLIENT_SECRET")
        self.refresh_token = os.environ.get("SP_API_REFRESH_TOKEN")
        self.marketplace_id = "A1VC38T7YXB528" # Japan
        self.access_token = None
        self.token_expiresat = datetime.now()

    async def _get_access_token(self):
        if self.access_token and datetime.now() < self.token_expiresat:
            return self.access_token

        logger.info("Access token expired or missing. Fetching new token...")
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        async with httpx.AsyncClient() as client:
            res = await client.post(LWA_ENDPOINT, data=data)
            if res.status_code == 200:
                token_data = res.json()
                self.access_token = token_data["access_token"]
                self.token_expiresat = datetime.now() + timedelta(seconds=token_data["expires_in"] - 60)
                logger.info("Successfully fetched new SP-API access token.")
                return self.access_token
            else:
                logger.error(f"Failed to fetch LWA token: {res.status_code} {res.text}")
                return None

    async def _headers(self):
        token = await self._get_access_token()
        return {
            "x-amz-access-token": token,
            "Content-Type": "application/json",
        }

    async def _request_with_retry(self, method, url, params=None, max_retries=3):
        """Async request with exponential backoff on 429 errors."""
        for i in range(max_retries):
            headers = await self._headers()
            async with httpx.AsyncClient() as client:
                try:
                    res = await client.request(method, url, headers=headers, params=params, timeout=30.0)
                    if res.status_code == 200:
                        return res.json()
                    elif res.status_code == 429:
                        wait_time = (2 ** i) + 1
                        logger.warning(f"Rate limited (429). Waiting {wait_time}s... (Retry {i+1}/{max_retries})")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"SP-API error {res.status_code}: {res.text}")
                        return None
                except Exception as e:
                    logger.error(f"Request exception: {e}")
                    await asyncio.sleep(1)
        return None

    # ---- High-level API Methods (Async) ----

    async def search_by_keyword(self, keyword, brand_name=None):
        """Search Amazon catalog by keyword. Returns a list of candidates."""
        import re
        clean_keyword = keyword[:80]
        clean_keyword = re.sub(r'[（(].*?[）)]', '', clean_keyword)
        clean_keyword = re.sub(r'対象年齢.*', '', clean_keyword)
        clean_keyword = re.sub(r'\s+', ' ', clean_keyword).strip()

        endpoint = f"{SP_API_URL}/catalog/2022-04-01/items"
        
        # Strategy 1: Search WITH brand filter
        common_params = {
            "marketplaceIds": self.marketplace_id,
            "keywords": clean_keyword,
            "includedData": "summaries,salesRanks", # Added salesRanks
            "pageSize": 5,
        }

        if brand_name and brand_name != "不明":
            params = {**common_params, "brandNames": brand_name}
            res_data = await self._request_with_retry("GET", endpoint, params=params)
            if res_data and res_data.get("items"):
                return self._parse_candidates(res_data.get("items"))
        
        # Strategy 2: Without brand filter
        res_data = await self._request_with_retry("GET", endpoint, params=common_params)
        if res_data and res_data.get("items"):
            return self._parse_candidates(res_data.get("items"))
            
        return []

    def _parse_candidates(self, items):
        candidates = []
        for item in items:
            asin = item.get("asin")
            brand = "不明"
            title = "不明"
            rank_str = "圏外"
            
            summaries = item.get("summaries", [])
            if summaries:
                brand = summaries[0].get("brand", "不明")
                title = summaries[0].get("itemName", "不明")
            
            # Extract Sales Rank (Category: Rank)
            ranks = item.get("salesRanks", [])
            if ranks:
                # Find the one with displayGroup or just use the first
                main_rank = ranks[0]
                cat = main_rank.get("displayGroup", "その他")
                val = main_rank.get("rank", "-")
                rank_str = f"{cat} {val}位"

            candidates.append({"asin": asin, "title": title, "brand": brand, "sales_rank": rank_str})
        return candidates

    async def get_competitive_pricing(self, asin):
        """Returns Buy Box price and Seller Count."""
        endpoint = f"{SP_API_URL}/products/pricing/v0/competitivePrice"
        params = {"MarketplaceId": self.marketplace_id, "ItemType": "Asin", "Asins": asin}
        res_data = await self._request_with_retry("GET", endpoint, params=params)
        
        price = 0
        seller_count = 0
        
        if res_data:
            payload = res_data.get("payload", [{}])[0]
            prod = payload.get("Product", {})
            comp = prod.get("CompetitivePricing", {})
            
            # 1. Price (Buy Box)
            prices = comp.get("CompetitivePrices", [])
            for p in prices:
                if p.get("CompetitivePriceId") == "1":
                    price = float(p.get("Price", {}).get("LandedPrice", {}).get("Amount", 0))
            
            # 2. Seller Count (New)
            offers = comp.get("NumberOfOfferListings", [])
            for o in offers:
                if o.get("Condition") == "New":
                    seller_count = int(o.get("Count", 0))
        
        return {"price": price, "seller_count": seller_count}

    async def get_fees_estimate(self, asin, price):
        endpoint = f"{SP_API_URL}/products/fees/v0/items/{asin}/feesEstimate"
        body = {
            "FeesEstimateRequest": {
                "MarketplaceId": self.marketplace_id,
                "IsAmazonFulfilled": True,
                "PriceToEstimateFees": {"ListingPrice": {"Amount": price, "CurrencyCode": "JPY"}},
                "Identifier": f"req_{int(time.time())}"
            }
        }
        # Special case for POST
        headers = await self._headers()
        async with httpx.AsyncClient() as client:
            try:
                res = await client.post(endpoint, headers=headers, json=body, timeout=20.0)
                if res.status_code == 200:
                    data = res.json()
                    fees = data.get("payload", {}).get("FeesEstimateResult", {}).get("FeesEstimate", {}).get("TotalFeesEstimate", {})
                    return float(fees.get("Amount", 0))
            except Exception:
                pass
        return price * 0.15 # Fallback (typical 15%)

    async def get_listing_restrictions(self, asin):
        endpoint = f"{SP_API_URL}/listings/2021-08-01/restrictions"
        params = {
            "asin": asin,
            "conditionType": "new",
            "sellerId": os.environ.get("SELLER_ID", ""),
            "marketplaceIds": self.marketplace_id,
            "reasonLocales": "ja_JP",
        }
        res_data = await self._request_with_retry("GET", endpoint, params=params)
        if res_data:
            restrictions = res_data.get("restrictions", [])
            if not restrictions:
                return "✅ 出品可能"
            msgs = []
            for r in restrictions:
                for reason in r.get("reasons", []):
                    msgs.append(reason.get("message", "制限あり"))
            return "⚠️ 制限: " + ", ".join(list(set(msgs)))
        return "⚪️ 制限確認不可"
