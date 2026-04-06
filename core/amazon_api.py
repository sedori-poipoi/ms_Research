import os
import time
import requests
import logging
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

CLIENT_ID = os.environ.get("AMAZON_CLIENT_ID")
CLIENT_SECRET = os.environ.get("AMAZON_CLIENT_SECRET")
REFRESH_TOKEN = os.environ.get("AMAZON_REFRESH_TOKEN")
SELLER_ID = os.environ.get("AMAZON_SELLER_ID")

SP_API_URL = "https://sellingpartnerapi-fe.amazon.com"
LWA_URL = "https://api.amazon.co.jp/auth/o2/token"

class AmazonSPAPI:
    def __init__(self):
        self.access_token = None
        self.token_expiry = 0
        self.marketplace_id = "A1VC38T7YXB528" # Japan

    def _get_access_token(self):
        if self.access_token and time.time() < self.token_expiry:
            return self.access_token

        logger.info("Access token expired or missing. Fetching new token...")
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": REFRESH_TOKEN,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET
        }
        res = requests.post(LWA_URL, data=payload)
        
        if res.status_code == 200:
            data = res.json()
            self.access_token = data.get("access_token")
            # Usually expires in 3600 seconds. Subtract 60 seconds as buffer.
            expires_in = data.get("expires_in", 3600)
            self.token_expiry = time.time() + expires_in - 60
            logger.info("Successfully fetched new SP-API access token.")
            return self.access_token
        else:
            logger.error(f"Failed to get access token: {res.text}")
            raise Exception("SP-API authentication failed")

    def _headers(self, additional_headers=None):
        headers = {
            "x-amz-access-token": self._get_access_token()
        }
        if additional_headers:
            headers.update(additional_headers)
        return headers

    def get_asin_from_jan(self, jan_code):
        endpoint = f"{SP_API_URL}/catalog/2022-04-01/items"
        params = {
            "marketplaceIds": self.marketplace_id,
            "identifiers": jan_code,
            "identifiersType": "EAN" # JAN is EAN
        }
        
        try:
            res = requests.get(endpoint, headers=self._headers(), params=params)
            if res.status_code == 200:
                items = res.json().get("items", [])
                if items:
                    item_data = items[0]
                    asin = item_data.get("asin")
                    brand = item_data.get("summaries", [{}])[0].get("brand", "不明")
                    return asin, brand
                return None, None
            elif res.status_code == 429:
                logger.warning("Rate limit exceeded for catalog/items. Sleeping...")
                time.sleep(2)
                return self.get_asin_from_jan(jan_code)
            elif res.status_code == 404:
                return None
            else:
                logger.error(f"Catalog API error for JAN {jan_code}: {res.text}")
                return None
        except Exception as e:
            logger.error(f"Network error in get_asin_from_jan: {e}")
            return None

    def get_competitive_pricing(self, asin):
        endpoint = f"{SP_API_URL}/products/pricing/v0/competitivePrice"
        params = {
            "MarketplaceId": self.marketplace_id,
            "ItemType": "Asin",
            "Asins": asin
        }
        
        try:
            res = requests.get(endpoint, headers=self._headers(), params=params)
            if res.status_code == 200:
                responses = res.json().get("payload", [])
                if responses:
                    data = responses[0]
                    if data.get("status") == "Success":
                        competitive_prices = data.get("Product", {}).get("CompetitivePricing", {}).get("CompetitivePrices", [])
                        if competitive_prices:
                            # Typically index 0 is the BuyBox price
                            # We get the LandedPrice (which is item + shipping)
                            return competitive_prices[0].get("Price", {}).get("LandedPrice", {}).get("Amount", 0)
            elif res.status_code == 429:
                time.sleep(1)
                return self.get_competitive_pricing(asin)
            return 0
        except Exception as e:
            logger.error(f"Network error in get_competitive_pricing: {e}")
            return 0

    def get_fees_estimate(self, asin, price):
        # Simplistic approach to fees using productPricing v0.
        endpoint = f"{SP_API_URL}/products/pricing/v0/price"
        # However, accurate fees require getMyFeesEstimateForSKU/ASIN which is in productFees v0
        fees_endpoint = f"{SP_API_URL}/products/fees/v0/items/{asin}/feesEstimate"
        
        payload = {
            "FeesEstimateRequest": {
                "MarketplaceId": self.marketplace_id,
                "IsAmazonFulfilled": True,
                "PriceToEstimateFees": {
                    "ListingPrice": {
                        "CurrencyCode": "JPY",
                        "Amount": price
                    }
                },
                "Identifier": str(asin)
            }
        }
        
        try:
            res = requests.post(fees_endpoint, headers=self._headers({"Content-Type": "application/json"}), json=payload)
            if res.status_code == 200:
                estimate = res.json().get("payload", {}).get("FeesEstimateResult", {}).get("FeesEstimate", {})
                return estimate.get("TotalFeesEstimate", {}).get("Amount", 0)
            elif res.status_code == 429:
                time.sleep(1)
                return self.get_fees_estimate(asin, price)
            return 0
        except Exception as e:
            logger.error(f"Error in get_fees_estimate: {e}")
            return 0

    def get_listing_restrictions(self, asin):
        # We need the sellerId for this
        if not SELLER_ID:
            logger.warning("SELLER_ID not found in .env, skipping restriction check.")
            return "不明"
            
        endpoint = f"{SP_API_URL}/listings/2021-08-01/restrictions"
        params = {
            "sellerId": SELLER_ID,
            "asin": asin,
            "marketplaceIds": self.marketplace_id
        }
        
        try:
            res = requests.get(endpoint, headers=self._headers(), params=params)
            if res.status_code == 200:
                restrictions = res.json().get("restrictions", [])
                if not restrictions:
                    return "⭕ 出品可"
                else:
                    return "❌ 制限あり"
            elif res.status_code == 429:
                time.sleep(1)
                return self.get_listing_restrictions(asin)
            else:
                logger.error(f"Error checking restrictions: {res.text}")
                return "❓ エラー"
        except Exception as e:
            logger.error(f"Network error in get_listing_restrictions: {e}")
            return "❓ エラー"

if __name__ == "__main__":
    # Simple test if executed directly
    api = AmazonSPAPI()
    test_jan = "3701129809334" # Bioderma example from early screenshots
    
    print("--- SP-API Test ---")
    asin = api.get_asin_from_jan(test_jan)
    print(f"JAN: {test_jan} -> ASIN: {asin}")
    
    if asin:
        price = api.get_competitive_pricing(asin)
        print(f"BuyBox Price: {price} JPY")
        
        fees = api.get_fees_estimate(asin, price)
        print(f"FBA Fees: {fees} JPY")
        
        restriction = api.get_listing_restrictions(asin)
        print(f"Listing Restriction: {restriction}")
