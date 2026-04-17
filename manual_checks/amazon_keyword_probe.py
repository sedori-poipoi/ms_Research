import asyncio

from core.amazon_api import AmazonSPAPI


QUERIES = [
    "明治 ほほえみ らくらくキューブ 1620g",
    "ほほえみ らくらくキューブ 1620g",
    "明治 ほほえみ らくらくキューブ",
    "明治 meiji ほほえみ らくらくキューブ 1620g",
]


async def main():
    api = AmazonSPAPI()

    if not all([api.client_id, api.client_secret, api.refresh_token]):
        print("Amazon SP-API credentials are not fully configured in .env")
        return

    for query in QUERIES:
        print(f"Query: {query}")
        try:
            candidates = await api.search_by_keyword(query, "明治")
        except Exception as exc:
            print(f"  Error: {exc}")
            continue

        if not candidates:
            print("  No candidates")
            continue

        for idx, candidate in enumerate(candidates[:5], start=1):
            asin = candidate.get("asin", "N/A")
            brand = candidate.get("brand", "不明")
            title = candidate.get("title", "不明")
            sales_rank = candidate.get("sales_rank", "圏外")
            print(f"  {idx}. {asin} | {brand} | {sales_rank}")
            print(f"     {title}")


if __name__ == "__main__":
    asyncio.run(main())
