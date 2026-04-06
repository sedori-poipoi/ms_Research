import sys
from core.amazon_api import AmazonSPAPI

api = AmazonSPAPI()
queries = [
    "明治 ほほえみ らくらくキューブ 1620g",
    "ほほえみ らくらくキューブ 1620g",
    "明治 ほほえみ らくらくキューブ",
    "明治 meiji ほほえみ らくらくキューブ 1620g"
]

for q in queries:
    try:
        asin, brand = api.search_by_keyword(q, "明治")
        print(f"Query: '{q}' -> ASIN: {asin}")
    except Exception as e:
        print(f"Error {q}: {e}")
