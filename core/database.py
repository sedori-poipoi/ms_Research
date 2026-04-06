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
            
            # --- Migration: Add keepa_url if missing ---
            try:
                cursor.execute("ALTER TABLE results ADD COLUMN keepa_url TEXT")
            except:
                pass 

            conn.commit()

    def save_result(self, res):
        """Save or update a single research result."""
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO results (
                    id, jan, asin, title, brand, price, amazon_price, 
                    profit, margin, roi, rank, sellers, restriction, 
                    judgment, amazon_url, keepa_url, ms_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                res["id"], res["jan"], res["asin"], res["title"], res["brand"],
                res["price"], res["amazon_price"], res["profit"], res["margin"],
                res["roi"], res["rank"], res["sellers"], res["restriction"],
                res["judgment"], res["amazon_url"], res.get("keepa_url", ""), res["ms_url"]
            ))
            conn.commit()

    def get_all_results(self, limit=100):
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
