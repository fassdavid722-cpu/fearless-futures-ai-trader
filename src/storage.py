import json
import os
import logging

logger = logging.getLogger("FearlessFutures.Storage")

class Storage:
    def __init__(self, file_path="data/state.json"):
        self.file_path = file_path
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

    def save_state(self, balance, position, trade_log, peak_balance, lessons=None):
        state = {
            "balance": balance,
            "position": position,
            "trade_log": trade_log,
            "peak_balance": peak_balance,
            "lessons": lessons or []
        }
        try:
            with open(self.file_path, 'w') as f:
                json.dump(state, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving state: {e}")

    def load_state(self):
        if not os.path.exists(self.file_path):
            return None
        try:
            with open(self.file_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading state: {e}")
            return None
