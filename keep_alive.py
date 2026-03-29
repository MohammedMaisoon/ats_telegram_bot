"""
keep_alive.py — Prevents Render free tier from sleeping
Runs a tiny Flask web server + pings itself every 10 minutes
"""

import os
import time
import logging
import requests
from flask import Flask
from threading import Thread

logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── Simple health check endpoint ─────────────────────────────
@app.route("/")
def home():
    return "🤖 ATS Bot is running!", 200

@app.route("/health")
def health():
    return {"status": "ok", "bot": "running"}, 200

# ── Self ping every 10 minutes ───────────────────────────────
def ping_self():
    """Pings own URL every 10 mins so Render never sleeps."""
    RENDER_URL = os.getenv("RENDER_URL", "")

    if not RENDER_URL:
        logger.warning("⚠️  RENDER_URL not set — self-ping disabled")
        return

    logger.info(f"🔁 Self-ping started → {RENDER_URL}")

    while True:
        time.sleep(600)  # 10 minutes
        try:
            response = requests.get(RENDER_URL, timeout=10)
            logger.info(f"✅ Self-ping OK — status {response.status_code}")
        except Exception as e:
            logger.warning(f"⚠️  Self-ping failed: {e}")

# ── Start Flask in background thread ────────────────────────
def run_flask():
    PORT = int(os.getenv("PORT", 8080))
    logger.info(f"🌐 Flask server starting on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, use_reloader=False)

# ── Main entry — call this from bot.py ──────────────────────
def keep_alive():
    """Start Flask server + self-ping. Call before bot.run_polling()"""

    # Thread 1 — Flask web server (required by Render)
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Thread 2 — Self ping to prevent sleep
    ping_thread = Thread(target=ping_self, daemon=True)
    ping_thread.start()

    logger.info("✅ Keep-alive system started!")
