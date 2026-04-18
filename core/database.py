import hashlib
import os
import sqlite3
from urllib.parse import urlsplit, urlunsplit

DEFAULT_DB_PATH = os.getenv("RESEARCH_DB_PATH", "data/history_research.db")


class ResearchDatabase:
    def __init__(self, db_path=DEFAULT_DB_PATH):
        self.db_path = db_path
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self.init_db()

    @staticmethod
    def _is_present(value):
        return value not in (None, "", "—")

    @staticmethod
    def normalize_source_url(url):
        if not url:
            return ""
        parts = urlsplit(url.strip())
        path = parts.path.rstrip("/") or parts.path
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))

    def make_result_id(self, res):
        source_url = self.normalize_source_url(res.get("ms_url", ""))
        jan = str(res.get("jan", "")).strip()
        asin = str(res.get("asin", "")).strip()

        if source_url:
            digest = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:16]
            return f"url_{digest}"
        if self._is_present(jan):
            return f"jan_{jan}"
        if self._is_present(asin):
            return f"asin_{asin}"

        title = str(res.get("title", "")).strip()
        brand = str(res.get("brand", "")).strip()
        fallback = f"{brand}|{title}".strip("|")
        digest = hashlib.sha1(fallback.encode("utf-8")).hexdigest()[:16]
        return f"item_{digest}"

    def _identity_conditions(self, res):
        conditions = []
        params = []

        jan = str(res.get("jan", "")).strip()
        asin = str(res.get("asin", "")).strip()
        source_url = self.normalize_source_url(res.get("ms_url", ""))

        if self._is_present(jan):
            conditions.append("jan = ?")
            params.append(jan)
        if self._is_present(asin):
            conditions.append("asin = ?")
            params.append(asin)
        if source_url:
            conditions.append("ms_url = ?")
            params.append(source_url)

        return conditions, params

    def _find_existing_status(self, cursor, res):
        conditions, params = self._identity_conditions(res)
        if not conditions:
            return None

        query = f"""
            SELECT id, is_favorite, is_checked
            FROM results
            WHERE {" OR ".join(conditions)}
            ORDER BY is_favorite DESC, is_checked DESC, created_at DESC
            LIMIT 1
        """
        cursor.execute(query, params)
        row = cursor.fetchone()
        return dict(row) if row else None

    def _find_existing_result(self, cursor, res):
        conditions, params = self._identity_conditions(res)
        if not conditions:
            return None

        query = f"""
            SELECT *
            FROM results
            WHERE {" OR ".join(conditions)}
            ORDER BY created_at DESC
            LIMIT 1
        """
        cursor.execute(query, params)
        row = cursor.fetchone()
        return dict(row) if row else None

    def _delete_duplicate_matches(self, cursor, current_id, res):
        conditions, params = self._identity_conditions(res)
        if not conditions:
            return

        query = f"""
            DELETE FROM results
            WHERE id != ?
              AND ({ " OR ".join(conditions) })
        """
        cursor.execute(query, (current_id, *params))

    def init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Create products table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS results (
                    id TEXT PRIMARY KEY,
                    jan TEXT,
                    asin TEXT,
                    title TEXT,
                    brand TEXT,
                    price INTEGER,
                    amazon_price INTEGER,
                    profit INTEGER,
                    margin TEXT,
                    roi TEXT,
                    rank TEXT,
                    sellers INTEGER,
                    restriction TEXT,
                    judgment TEXT,
                    amazon_url TEXT,
                    keepa_url TEXT,
                    ms_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # --- Migrations: Add columns if missing ---
            migrations = [
                ("keepa_url", "TEXT"),
                ("in_stock", "INTEGER DEFAULT 1"),
                ("is_favorite", "INTEGER DEFAULT 0"),
                ("is_checked", "INTEGER DEFAULT 0"),
                ("monthly_sales", "TEXT DEFAULT 'データなし'"),
                ("drops_30", "INTEGER DEFAULT 0"),
                ("price_stability", "TEXT DEFAULT '不明'"),
                ("filter_status", "TEXT DEFAULT 'visible'"),
                ("filter_reason", "TEXT DEFAULT ''"),
                ("restriction_code", "TEXT DEFAULT ''"),
                ("approval_url", "TEXT DEFAULT ''"),
                ("source_site", "TEXT DEFAULT ''"),
                ("source_site_label", "TEXT DEFAULT ''"),
                ("source_category", "TEXT DEFAULT ''"),
                ("source_category_label", "TEXT DEFAULT ''"),
                ("match_method", "TEXT DEFAULT ''"),
                ("match_label", "TEXT DEFAULT ''"),
                ("match_details", "TEXT DEFAULT ''"),
                ("match_score", "INTEGER DEFAULT 0"),
                ("watch_reason", "TEXT DEFAULT ''"),
                ("previous_profit", "INTEGER DEFAULT 0"),
                ("profit_delta", "INTEGER DEFAULT 0"),
                ("previous_amazon_price", "INTEGER DEFAULT 0"),
                ("amazon_price_delta", "INTEGER DEFAULT 0"),
                ("previous_restriction", "TEXT DEFAULT ''"),
                ("change_summary", "TEXT DEFAULT ''"),
            ]
            for col_name, col_type in migrations:
                try:
                    cursor.execute(f"ALTER TABLE results ADD COLUMN {col_name} {col_type}")
                except:
                    pass

            conn.commit()

    def save_result(self, res):
        """Save or update a single research result. Preserves user flags (favorite/checked)."""
        payload = dict(res)
        payload["ms_url"] = self.normalize_source_url(payload.get("ms_url", ""))
        payload["id"] = self.make_result_id(payload)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            existing = self._find_existing_status(cursor, payload)
            previous_row = self._find_existing_result(cursor, payload)
            payload["is_favorite"] = existing["is_favorite"] if existing else int(payload.get("is_favorite", 0))
            payload["is_checked"] = existing["is_checked"] if existing else int(payload.get("is_checked", 0))

            columns = [
                "id", "jan", "asin", "title", "brand", "price", "amazon_price",
                "profit", "margin", "roi", "rank", "sellers", "restriction",
                "judgment", "amazon_url", "keepa_url", "ms_url", "in_stock",
                "is_favorite", "is_checked", "monthly_sales", "drops_30",
                "price_stability", "filter_status", "filter_reason",
                "restriction_code", "approval_url", "source_site",
                "source_site_label", "source_category", "source_category_label",
                "match_method", "match_label", "match_details", "match_score",
                "watch_reason", "previous_profit", "profit_delta",
                "previous_amazon_price", "amazon_price_delta",
                "previous_restriction", "change_summary",
            ]
            values = (
                payload["id"], payload.get("jan", "—"), payload.get("asin", "—"),
                payload.get("title", "不明"), payload.get("brand", "不明"),
                payload.get("price", 0), payload.get("amazon_price", 0),
                payload.get("profit", 0), payload.get("margin", "0%"),
                payload.get("roi", "0%"), payload.get("rank", "—"),
                payload.get("sellers", 0), payload.get("restriction", "確認中"),
                payload.get("judgment", "判定不可"),
                payload.get("amazon_url", "#"), payload.get("keepa_url", "#"),
                payload.get("ms_url", ""), payload.get("in_stock", 1),
                payload.get("is_favorite", 0), payload.get("is_checked", 0),
                payload.get("monthly_sales", "データなし"),
                payload.get("drops_30", 0),
                payload.get("price_stability", "不明"),
                payload.get("filter_status", "visible"),
                payload.get("filter_reason", ""),
                payload.get("restriction_code", ""),
                payload.get("approval_url", ""),
                payload.get("source_site", ""),
                payload.get("source_site_label", ""),
                payload.get("source_category", ""),
                payload.get("source_category_label", ""),
                payload.get("match_method", ""),
                payload.get("match_label", ""),
                payload.get("match_details", ""),
                payload.get("match_score", 0),
                payload.get("watch_reason", ""),
                payload.get("previous_profit", 0),
                payload.get("profit_delta", 0),
                payload.get("previous_amazon_price", 0),
                payload.get("amazon_price_delta", 0),
                payload.get("previous_restriction", ""),
                payload.get("change_summary", ""),
            )
            placeholders = ", ".join(["?"] * len(columns))
            cursor.execute(
                f"INSERT OR REPLACE INTO results ({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )
            self._delete_duplicate_matches(cursor, payload["id"], payload)
            conn.commit()
        payload["_previous_row"] = previous_row
        return payload

    def find_matching_result(self, res):
        payload = dict(res)
        payload["ms_url"] = self.normalize_source_url(payload.get("ms_url", ""))
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            row = self._find_existing_result(cursor, payload)
            return dict(row) if row else None

    def get_all_results(self, limit=200):
        """Fetch historical results."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM results ORDER BY created_at DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def get_brand_recheck_candidates(self, limit=200):
        """Fetch rows that are likely to benefit from a no-brand recheck."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT *
                FROM results
                WHERE asin IS NOT NULL
                  AND asin != ''
                  AND asin != '—'
                  AND (
                        brand IS NULL
                     OR TRIM(brand) = ''
                     OR brand IN ('不明', 'unknown', 'Unknown', '—', '-', 'NETSEA')
                     OR filter_reason = 'ノーブランド品'
                  )
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def delete_result(self, res_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM results WHERE id = ?", (res_id,))
            conn.commit()

    def update_result_status(self, res_id, field, value):
        """Update is_favorite or is_checked."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            safe_field = "is_favorite" if field == "favorite" else "is_checked"
            cursor.execute(f"UPDATE results SET {safe_field} = ? WHERE id = ?", (int(value), res_id))
            conn.commit()

    def update_result_fields(self, res_id, updates):
        """Update a safe subset of mutable result fields."""
        if not updates:
            return

        allowed_fields = {
            "brand",
            "filter_status",
            "filter_reason",
            "judgment",
        }
        safe_updates = {key: value for key, value in updates.items() if key in allowed_fields}
        if not safe_updates:
            return

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            assignments = ", ".join(f"{field} = ?" for field in safe_updates)
            params = list(safe_updates.values()) + [res_id]
            cursor.execute(f"UPDATE results SET {assignments} WHERE id = ?", params)
            conn.commit()

    def clear_all_results(self):
        """Clear transient results but keep user-marked favorites and checked items."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM results WHERE is_favorite = 0 AND is_checked = 0")
            conn.commit()
