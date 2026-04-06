import sys
import requests
from core.amazon_api import AmazonSPAPI, SP_API_URL

api = AmazonSPAPI()
asins = ["B0DV8ZBDQY", "B01N6EL2MJ"]

for asin in asins:
    endpoint = f"{SP_API_URL}/catalog/2022-04-01/items/{asin}"
    params = {"marketplaceIds": api.marketplace_id, "includedData": "summaries"}
    res = requests.get(endpoint, headers=api._headers(), params=params)
    item = res.json()
    if "summaries" in item:
        print(f"{asin}: {item['summaries'][0].get('itemName')}")
    else:
        print(f"{asin}: No summaries")
