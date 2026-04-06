import json
import os
from datetime import datetime, timedelta

class HistoryManager:
    def __init__(self, history_file='history.json'):
        self.history_file = history_file
        self.history = self._load_history()

    def _load_history(self):
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def _save_history(self):
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving history: {e}")

    def is_researched(self, jan, expiry_hours=24):
        """Check if JAN was researched within the last expiry_hours."""
        if jan in self.history:
            last_time = datetime.fromisoformat(self.history[jan])
            if datetime.now() - last_time < timedelta(hours=expiry_hours):
                return True
        return False

    def add_to_history(self, jan):
        """Mark JAN as researched now."""
        self.history[jan] = datetime.now().isoformat()
        self._save_history()

    def clear_old_history(self, days=7):
        """Remove entries older than specified days to keep the file small."""
        cutoff = datetime.now() - timedelta(days=days)
        new_history = {
            jan: ts for jan, ts in self.history.items()
            if datetime.fromisoformat(ts) > cutoff
        }
        self.history = new_history
        self._save_history()
