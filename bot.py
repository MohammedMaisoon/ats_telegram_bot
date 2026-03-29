"""
ATS Score Telegram Bot
Main bot file - handles all Telegram interactions
Includes keep-alive for Render free tier (no sleep!)
"""

import os
import json
import asyncio
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from storage import RedisStorage
from scanner import ATSScanner
from keep_alive import keep_alive          # ← Render no-sleep

# ── Load env variables ──────────────────────────────────────
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# ── Logging ─────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── Conversation States ──────────────────────────────────────
(
    WAITING_RESUME,
    WAITING_JD,
) = range(2)

# ── Storage & Scanner ────────────────────────────────────────
storage = RedisStorage()
scanner = ATSScanner()


# ════════════════════════════════════════════════════════════
#  /start  — Entry point (goes straight to resume)
# ════════════════════════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    first_name = update.effective_user.first_name

    await update.message.reply_text(
        f"👋 Hello *{first_name}!* Welcome to *ATS Score Bot*\n\n"
        f"I'll check how well your resume matches a job description!\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📄 Please send your *Resume* as text to begin:",
        parse_mode="Markdown"
    )
    return WAITING_RESUME


# ════════════════════════════════════════════════════════════
#  Resume Handler
# ════════════════════════════════════════════════════════════
async def receive_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if update.message.document:
        doc = update.message.document
        if doc.file_size > 500_000:
            await update.message.reply_text(
                "❌ File too large. Please paste resume as text."
            )
            return WAITING_RESUME
        file = await doc.get_file()
        file_bytes = await file.download_as_bytearray()
        resume_text = file_bytes.decode("utf-8", errors="ignore")
    else:
        resume_text = update.message.text

    if len(resume_text.strip()) < 50:
        await update.message.reply_text(
            "❌ Resume too short. Please send full resume text."
        )
        return WAITING_RESUME

    storage.save_temp(user_id, "resume", resume_text)

    await update.message.reply_text(
        "✅ *Resume received!*\n\n"
        "📋 Now send the *Job Description* (paste the full JD text):",
        parse_mode="Markdown"
    )
    return WAITING_JD


# ════════════════════════════════════════════════════════════
#  JD Handler — Run the ATS scan!
# ════════════════════════════════════════════════════════════
async def receive_jd_and_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    jd_text = update.message.text

    if len(jd_text.strip()) < 50:
        await update.message.reply_text(
            "❌ JD too short. Please send the full job description."
        )
        return WAITING_JD

    resume_text = storage.get_temp(user_id, "resume")
    if not resume_text:
        await update.message.reply_text("❌ Resume lost. Please /start again.")
        return ConversationHandler.END

    status_msg = await update.message.reply_text(
        "⏳ *Checking your ATS score...*\n\n"
        "🔄 Running analysis (30-60 seconds)\n"
        "Please wait...",
        parse_mode="Markdown"
    )

    try:
        # Always use master account silently — no user cookies needed
        result = await scanner.scan(
            resume_text=resume_text,
            jd_text=jd_text,
            cookies=None,
            user_id=user_id
        )

        storage.delete_temp(user_id, "resume")

        score   = result.get("score", "N/A")
        matched = result.get("matched_keywords", [])
        missing = result.get("missing_keywords", [])
        error   = result.get("error")

        if error:
            await status_msg.edit_text(
                f"❌ Something went wrong. Please try /start again.\n"
                f"Error: {str(error)[:100]}"
            )
            return ConversationHandler.END

        try:
            score_int = int(str(score).replace("%", "").strip())
            if score_int >= 75:
                emoji   = "🟢"
                verdict = "Excellent Match!"
            elif score_int >= 55:
                emoji   = "🟡"
                verdict = "Good Match"
            elif score_int >= 35:
                emoji   = "🟠"
                verdict = "Partial Match"
            else:
                emoji   = "🔴"
                verdict = "Poor Match"
        except:
            emoji   = "📊"
            verdict = "Score Retrieved"

        matched_str = ", ".join(matched[:12]) if matched else "None found"
        missing_str = ", ".join(missing[:12]) if missing else "None — great job!"

        response = (
            f"{emoji} *ATS Score: {score}%*\n"
            f"📝 Verdict: _{verdict}_\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ *Matched Keywords ({len(matched)}):*\n"
            f"{matched_str}\n\n"
            f"❌ *Missing Keywords ({len(missing)}):*\n"
            f"{missing_str}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 *Tip:* Add missing keywords naturally to boost your score!\n\n"
            f"🔄 Send /start to check another resume."
        )

        await status_msg.edit_text(response, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Scan error for user {user_id}: {e}")
        await status_msg.edit_text(
            f"❌ Something went wrong. Please try /start again.\n"
            f"Error: {str(e)[:100]}"
        )

    return ConversationHandler.END


# ════════════════════════════════════════════════════════════
#  /cancel
# ════════════════════════════════════════════════════════════
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled.\n\nUse /start to begin again.")
    return ConversationHandler.END


# ════════════════════════════════════════════════════════════
#  Main
# ════════════════════════════════════════════════════════════
def main():
    # ✅ Start keep-alive FIRST (prevents Render sleep)
    keep_alive()

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAITING_RESUME: [
                MessageHandler(
                    (filters.TEXT | filters.Document.TXT) & ~filters.COMMAND,
                    receive_resume
                )
            ],
            WAITING_JD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_jd_and_scan)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)

    logger.info("🤖 ATS Bot is running!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
