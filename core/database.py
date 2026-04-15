import sqlite3
import os
import json
from datetime import datetime

DB_PATH = "data/history_research.db"

class ResearchDatabase:
    def __init__(self):
        os.makedirs("data", exist_ok=True)
        self.init_db()

    def init_db(self):
        with sqlite3.connect(DB_PATH) as conn:
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
            ]
            for col_name, col_type in migrations:
                try:
                    cursor.execute(f"ALTER TABLE results ADD COLUMN {col_name} {col_type}")
                except:
                    pass

            conn.commit()

    def save_result(self, res):
        """Save or update a single research result. Preserves user flags (favorite/checked)."""
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO results (
                    id, jan, asin, title, brand, price, amazon_price, 
                    profit, margin, roi, rank, sellers, restriction, 
                    judgment, amazon_url, keepa_url, ms_url, in_stock,
                    is_favorite, is_checked, monthly_sales, drops_30,
                    price_stability, filter_status, filter_reason,
                    restriction_code, approval_url
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    COALESCE((SELECT is_favorite FROM results WHERE id=?), 0),
                    COALESCE((SELECT is_checked FROM results WHERE id=?), 0),
                    ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                res["id"], res.get("jan", "—"), res.get("asin", "—"),
                res.get("title", "不明"), res.get("brand", "不明"),
                res.get("price", 0), res.get("amazon_price", 0),
                res.get("profit", 0), res.get("margin", "0%"),
                res.get("roi", "0%"), res.get("rank", "—"),
                res.get("sellers", 0), res.get("restriction", "確認中"),
                res.get("judgment", "判定不可"),
                res.get("amazon_url", "#"), res.get("keepa_url", "#"),
                res.get("ms_url", ""), res.get("in_stock", 1),
                res["id"], res["id"],
                res.get("monthly_sales", "データなし"),
                res.get("drops_30", 0),
                res.get("price_stability", "不明"),
                res.get("filter_status", "visible"),
                res.get("filter_reason", ""),
                res.get("restriction_code", ""),
                res.get("approval_url", "")
            ))
            conn.commit()

    def get_all_results(self, limit=200):
        """Fetch historical results."""
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM results ORDER BY created_at DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def delete_result(self, res_id):
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM results WHERE id = ?", (res_id,))
            conn.commit()

    def update_result_status(self, res_id, field, value):
        """Update is_favorite or is_checked."""
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            safe_field = "is_favorite" if field == "favorite" else "is_checked"
            cursor.execute(f"UPDATE results SET {safe_field} = ? WHERE id = ?", (int(value), res_id))
            conn.commit()

    def clear_all_results(self):
        """Clear all non-favorite results from the display table."""
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM results WHERE is_favorite = 0")
            conn.commit()

