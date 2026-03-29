"""
storage.py — Encrypted Redis storage for cookies & temp data
"""

import os
import json
import redis
import logging
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

REDIS_URL   = os.getenv("REDIS_URL", "redis://localhost:6379")
ENCRYPT_KEY = os.getenv("ENCRYPT_KEY")          # 32-byte Fernet key
COOKIE_TTL  = 60 * 60 * 24 * 30                 # 30 days in seconds
TEMP_TTL    = 60 * 60                            # 1 hour


class RedisStorage:
    def __init__(self):
        self.r = redis.from_url(REDIS_URL, decode_responses=True)
        if ENCRYPT_KEY:
            self.cipher = Fernet(ENCRYPT_KEY.encode())
        else:
            # Auto-generate key if not set (warn user)
            key = Fernet.generate_key()
            self.cipher = Fernet(key)
            logger.warning(
                "⚠️  No ENCRYPT_KEY in .env — generated temporary key. "
                "Set ENCRYPT_KEY in .env for persistent encryption!"
            )

    # ── Encryption helpers ───────────────────────────────────
    def _encrypt(self, text: str) -> str:
        return self.cipher.encrypt(text.encode()).decode()

    def _decrypt(self, token: str) -> str:
        return self.cipher.decrypt(token.encode()).decode()

    # ── Cookies ─────────────────────────────────────────────
    def save_cookies(self, user_id: int, cookies_json: str):
        """Encrypt and store cookies with 30-day TTL."""
        encrypted = self._encrypt(cookies_json)
        self.r.setex(f"cookies:{user_id}", COOKIE_TTL, encrypted)
        size = len(cookies_json)
        logger.info(f"Saved cookies for user {user_id} ({size} bytes)")

    def get_cookies(self, user_id: int):
        """Load and decrypt cookies. Returns list or None."""
        raw = self.r.get(f"cookies:{user_id}")
        if not raw:
            return None
        try:
            decrypted = self._decrypt(raw)
            return json.loads(decrypted)
        except Exception as e:
            logger.error(f"Cookie decrypt error for {user_id}: {e}")
            return None

    def has_cookies(self, user_id: int) -> bool:
        return self.r.exists(f"cookies:{user_id}") == 1

    def delete_cookies(self, user_id: int):
        self.r.delete(f"cookies:{user_id}")
        logger.info(f"Deleted cookies for user {user_id}")

    # ── Master account flag ──────────────────────────────────
    def set_use_master(self, user_id: int):
        self.r.setex(f"master:{user_id}", COOKIE_TTL, "1")

    def get_use_master(self, user_id: int) -> bool:
        return self.r.get(f"master:{user_id}") == "1"

    def delete_use_master(self, user_id: int):
        self.r.delete(f"master:{user_id}")

    # ── Temp data (resume/jd in transit) ────────────────────
    def save_temp(self, user_id: int, key: str, value: str):
        """Store temp data (resume/jd) for 1 hour only."""
        self.r.setex(f"temp:{user_id}:{key}", TEMP_TTL, value)

    def get_temp(self, user_id: int, key: str):
        return self.r.get(f"temp:{user_id}:{key}")

    def delete_temp(self, user_id: int, key: str):
        self.r.delete(f"temp:{user_id}:{key}")
