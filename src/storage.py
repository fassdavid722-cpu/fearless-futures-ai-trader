import json
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger("FearlessFutures.Storage")

class Storage:
    def __init__(self):
        self.data_dir = os.getenv("PERSISTENT_VOLUME_PATH", "data")
        self.state_path = os.path.join(self.data_dir, "state.json")
        self.journal_path = os.path.join(self.data_dir, "journal.jsonl")
        os.makedirs(self.data_dir, exist_ok=True)

    # ── State persistence ──────────────────────────────────────────────────────

    def save_state(self, balance, position, trade_log, peak_balance, lessons=None, ai_log=None):
        state = {
            "balance": balance,
            "position": position,
            "trade_log": trade_log,
            "peak_balance": peak_balance,
            "lessons": lessons or [],
            "ai_log": ai_log or [],
            "saved_at": datetime.now(timezone.utc).isoformat()
        }
        try:
            with open(self.state_path, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Save state error: {e}")

    def load_state(self):
        if not os.path.exists(self.state_path):
            return None
        try:
            with open(self.state_path) as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Load state error: {e}")
            return None

    # ── Trade journal (append-only) ────────────────────────────────────────────

    def append_journal(self, trade: dict):
        try:
            with open(self.journal_path, 'a') as f:
                f.write(json.dumps(trade) + '\n')
        except Exception as e:
            logger.error(f"Journal append error: {e}")

    def read_journal(self, last_n=20):
        if not os.path.exists(self.journal_path):
            return []
        try:
            with open(self.journal_path) as f:
                lines = f.readlines()
            return [json.loads(l) for l in lines[-last_n:]]
        except Exception as e:
            logger.error(f"Journal read error: {e}")
            return []
