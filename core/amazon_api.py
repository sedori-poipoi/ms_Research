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
        self.client_id = os.environ.get("AMAZON_CLIENT_ID")
        self.client_secret = os.environ.get("AMAZON_CLIENT_SECRET")
        self.refresh_token = os.environ.get("AMAZON_REFRESH_TOKEN")
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

    async def search_by_jan(self, jan_code):
        """Search Amazon catalog by JAN/EAN code. Returns a list of candidates."""
        if not jan_code or not jan_code.strip().isdigit():
            return []
        
        endpoint = f"{SP_API_URL}/catalog/2022-04-01/items"
        params = {
            "marketplaceIds": self.marketplace_id,
            "identifiers": jan_code.strip(),
            "identifiersType": "EAN",
            "includedData": "summaries,salesRanks",
            "pageSize": 5,
        }
        
        res_data = await self._request_with_retry("GET", endpoint, params=params)
        if res_data and res_data.get("items"):
            return self._parse_candidates(res_data.get("items"))
        return []

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
            "includedData": "summaries,salesRanks",
            "pageSize": 10,
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
            # salesRanks structure: [{"classificationId": ..., "ranks": [{"value": 1234, ...}]}]
            ranks = item.get("salesRanks", [])
            if ranks:
                for rank_group in ranks:
                    display_group = rank_group.get("classificationId", "その他")
                    rank_list = rank_group.get("ranks", [])
                    if rank_list:
                        val = rank_list[0].get("value", rank_list[0].get("rank", "-"))
                        if val and val != "-":
                            rank_str = f"{display_group} {val}位"
                            break
                    # Fallback for flat structure
                    flat_rank = rank_group.get("rank")
                    flat_dg = rank_group.get("displayGroup", "その他")
                    if flat_rank and flat_rank != "-":
                        rank_str = f"{flat_dg} {flat_rank}位"
                        break

            candidates.append({"asin": asin, "title": title, "brand": brand, "sales_rank": rank_str})
        return candidates

    async def get_competitive_pricing(self, asin):
        """Returns Buy Box price and Seller Count."""
        endpoint = f"{SP_API_URL}/products/pricing/v0/competitivePrice"
        params = {"MarketplaceId": self.marketplace_id, "ItemType": "Asin", "Asins": asin}
        res_data = await self._request_with_retry("GET", endpoint, params=params)
        
        listing_price = 0
        shipping = 0
        landed_price = 0
        seller_count = 0
        
        if res_data:
            payload_list = res_data.get("payload", [])
            if payload_list:
                payload = payload_list[0]
                prod = payload.get("Product", {})
                comp = prod.get("CompetitivePricing", {})
                
                # 1. Price (Buy Box)
                prices = comp.get("CompetitivePrices", [])
                for p in prices:
                    if p.get("CompetitivePriceId") == "1":
                        p_data = p.get("Price", {})
                        landed_price = float(p_data.get("LandedPrice", {}).get("Amount", 0))
                        listing_price = float(p_data.get("ListingPrice", {}).get("Amount", 0))
                        shipping = float(p_data.get("Shipping", {}).get("Amount", 0))
                
                # 2. Seller Count (New)
                offers = comp.get("NumberOfOfferListings", [])
                for o in offers:
                    cond = str(o.get("Condition", "")).lower()
                    if cond == "new":
                        seller_count = int(o.get("Count", 0))
        
        # Fallback: Price 0
        if landed_price == 0:
            logger.info(f"Competitive pricing for {asin} returned 0. Trying lowest priced offers fallback...")
            lowest_data = await self.get_lowest_priced_offers_for_asin(asin)
            landed_price = lowest_data.get("landed_price", 0)
            listing_price = lowest_data.get("listing_price", 0)
            shipping = lowest_data.get("shipping", 0)
            if seller_count == 0:
                seller_count = lowest_data.get("seller_count", 0)

        return {
            "price": landed_price, 
            "listing_price": listing_price,
            "shipping": shipping,
            "seller_count": seller_count
        }

    async def get_lowest_priced_offers_for_asin(self, asin):
        """Fallback: gets price from lowest priced offers (useful for backordered items)."""
        endpoint = f"{SP_API_URL}/products/pricing/v0/items/{asin}/offers"
        params = {
            "MarketplaceId": self.marketplace_id,
            "ItemCondition": "New",
            "CustomerType": "Consumer"
        }
        res_data = await self._request_with_retry("GET", endpoint, params=params)
        
        listing_price = 0
        shipping = 0
        landed_price = 0
        seller_count = 0
        
        if res_data:
            payload = res_data.get("payload", {})
            summary = payload.get("Summary", {})
            
            # Get Lowest Prices (LandedPrice)
            lowest_prices = summary.get("LowestPrices", [])
            if lowest_prices:
                p_data = lowest_prices[0]
                landed_price = float(p_data.get("LandedPrice", {}).get("Amount", 0))
                listing_price = float(p_data.get("ListingPrice", {}).get("Amount", 0))
                shipping = float(p_data.get("Shipping", {}).get("Amount", 0))
            
            # Get Total Offer Count
            counts = summary.get("NumberOfOffers", [])
            for c in counts:
                cond = str(c.get("condition", "")).lower()
                if cond == "new":
                    seller_count = int(c.get("count", 0))
            
            if seller_count == 0:
                offers_list = payload.get("Offers", [])
                if offers_list:
                    seller_count = len(offers_list)
        
        return {
            "landed_price": landed_price, 
            "listing_price": listing_price,
            "shipping": shipping,
            "seller_count": seller_count
        }

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
        """Check listing restrictions for a given ASIN."""
        result = {
            "status": "⚪️ 制限確認不可",
            "reason_code": "",
            "approval_url": ""
        }
        try:
            endpoint = f"{SP_API_URL}/listings/2021-08-01/restrictions"
            params = {
                "asin": asin,
                "conditionType": "new_new",
                "sellerId": os.environ.get("AMAZON_SELLER_ID", ""),
                "marketplaceIds": self.marketplace_id,
                "reasonLocales": "ja_JP",
            }
            res_data = await self._request_with_retry("GET", endpoint, params=params)
            if res_data:
                restrictions = res_data.get("restrictions", [])
                if not restrictions:
                    result["status"] = "✅ 出品可能"
                    return result
                
                msgs = []
                reason_codes = []
                approval_urls = []
                
                for r in restrictions:
                    for reason in r.get("reasons", []):
                        msgs.append(reason.get("message", "制限あり"))
                        
                        code = reason.get("reasonCode", "")
                        if code:
                            reason_codes.append(code)
                            
                        links = reason.get("links", [])
                        for link in links:
                            if link.get("verb", "").upper() == "GET" and link.get("resource"):
                                approval_urls.append(link.get("resource"))
                
                result["status"] = "⚠️ 制限: " + ", ".join(list(set(msgs)))
                
                if "APPROVAL_REQUIRED" in reason_codes:
                    result["reason_code"] = "APPROVAL_REQUIRED"
                elif "NOT_ELIGIBLE" in reason_codes:
                    result["reason_code"] = "NOT_ELIGIBLE"
                elif reason_codes:
                    result["reason_code"] = reason_codes[0]
                    
                if approval_urls:
                    url = approval_urls[0]
                    if not url.startswith("http"):
                        url = f"https://sellercentral.amazon.co.jp{url}"
                    result["approval_url"] = url

                return result
        except Exception as e:
            logger.error(f"Listing restrictions check failed for {asin}: {e}")
        return result

