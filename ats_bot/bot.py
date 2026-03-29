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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
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
    WAITING_ACCOUNT_CHOICE,
    WAITING_COOKIES,
    WAITING_RESUME,
    WAITING_JD,
) = range(4)

# ── Storage & Scanner ────────────────────────────────────────
storage = RedisStorage()
scanner = ATSScanner()


# ════════════════════════════════════════════════════════════
#  /start  — Entry point
# ════════════════════════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name

    has_cookies = storage.has_cookies(user_id)

    if has_cookies:
        await update.message.reply_text(
            f"👋 Welcome back, *{first_name}!*\n\n"
            f"✅ Your SkillSyncer account is connected.\n\n"
            f"📄 Please send your *Resume* as text to begin.",
            parse_mode="Markdown"
        )
        return WAITING_RESUME

    else:
        keyboard = [
            [InlineKeyboardButton("✅ Yes, I have an account", callback_data="has_account")],
            [InlineKeyboardButton("❌ No account (use shared)", callback_data="no_account")],
        ]
        await update.message.reply_text(
            f"👋 Hello *{first_name}!* Welcome to *ATS Score Bot*\n\n"
            f"I'll check how well your resume matches a job description "
            f"using SkillSyncer!\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Do you have a *SkillSyncer* account?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return WAITING_ACCOUNT_CHOICE


# ════════════════════════════════════════════════════════════
#  Account Choice Handler
# ════════════════════════════════════════════════════════════
async def account_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "has_account":
        await query.message.reply_text(
            "🍪 *One-Time Cookie Setup*\n\n"
            "Since SkillSyncer uses login, we use your browser cookies.\n"
            "This is a *one-time setup only!*\n\n"
            "📋 *Steps to get your cookies:*\n\n"
            "1️⃣ Open *Chrome* on your PC/laptop\n"
            "2️⃣ Go to 👉 skillsyncer.com and *log in*\n"
            "3️⃣ Install extension: *Cookie-Editor*\n"
            "   (search in Chrome Web Store)\n"
            "4️⃣ Click the Cookie-Editor icon\n"
            "5️⃣ Click *Export → Export as JSON*\n"
            "6️⃣ It copies to clipboard automatically\n"
            "7️⃣ *Paste it here* in this chat\n\n"
            "🔒 _Your cookies are encrypted and stored securely._\n"
            "⏳ _They auto-delete after 30 days._",
            parse_mode="Markdown"
        )
        return WAITING_COOKIES

    elif query.data == "no_account":
        storage.set_use_master(user_id)
        await query.message.reply_text(
            "✅ *Using our shared SkillSyncer account!*\n\n"
            "⚠️ Note: Shared account has limited scans.\n"
            "For unlimited scans, create a free account at skillsyncer.com\n\n"
            "📄 Now send your *Resume* as text:",
            parse_mode="Markdown"
        )
        return WAITING_RESUME


# ════════════════════════════════════════════════════════════
#  Cookie Handler
# ════════════════════════════════════════════════════════════
async def save_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cookies_text = update.message.text.strip()

    try:
        cookies_data = json.loads(cookies_text)
        if not isinstance(cookies_data, list):
            raise ValueError("Not a list")
    except (json.JSONDecodeError, ValueError):
        await update.message.reply_text(
            "❌ *Invalid cookies format!*\n\n"
            "Please make sure you:\n"
            "• Used Cookie-Editor extension\n"
            "• Clicked *Export as JSON*\n"
            "• Pasted the full JSON text\n\n"
            "Try again 👇",
            parse_mode="Markdown"
        )
        return WAITING_COOKIES

    size_kb = len(cookies_text) / 1024
    storage.save_cookies(user_id, cookies_text)

    await update.message.reply_text(
        f"✅ *Cookies saved successfully!*\n"
        f"📦 Size: {size_kb:.1f} KB\n"
        f"⏳ Auto-expires in 30 days\n\n"
        f"You won't need to do this again until they expire.\n\n"
        f"📄 Now send your *Resume* as text:",
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
        "🔄 Loading your SkillSyncer session\n"
        "🤖 Running analysis (30-60 seconds)\n"
        "Please wait...",
        parse_mode="Markdown"
    )

    try:
        use_master = storage.get_use_master(user_id)
        if use_master:
            cookies = None
        else:
            cookies = storage.get_cookies(user_id)
            if not cookies:
                await status_msg.edit_text(
                    "⚠️ *Your SkillSyncer session expired!*\n\n"
                    "Please send fresh cookies:\n"
                    "1️⃣ Go to skillsyncer.com (logged in)\n"
                    "2️⃣ Cookie-Editor → Export as JSON\n"
                    "3️⃣ Paste here",
                    parse_mode="Markdown"
                )
                return WAITING_COOKIES

        result = await scanner.scan(
            resume_text=resume_text,
            jd_text=jd_text,
            cookies=cookies,
            user_id=user_id
        )

        storage.delete_temp(user_id, "resume")

        score   = result.get("score", "N/A")
        matched = result.get("matched_keywords", [])
        missing = result.get("missing_keywords", [])
        error   = result.get("error")

        if error == "session_expired":
            storage.delete_cookies(user_id)
            await status_msg.edit_text(
                "⚠️ *Session expired!*\n\nPlease send fresh cookies.",
                parse_mode="Markdown"
            )
            return WAITING_COOKIES

        if error:
            await status_msg.edit_text(f"❌ Error: {error}\n\nTry /start again.")
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
#  /reset
# ════════════════════════════════════════════════════════════
async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    storage.delete_cookies(user_id)
    storage.delete_use_master(user_id)
    await update.message.reply_text(
        "🗑️ *Reset complete!*\n\n"
        "Your saved session has been cleared.\n"
        "Use /start to set up again.",
        parse_mode="Markdown"
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
            WAITING_ACCOUNT_CHOICE: [
                CallbackQueryHandler(account_choice)
            ],
            WAITING_COOKIES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_cookies)
            ],
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
            CommandHandler("reset", reset),
            CommandHandler("start", start),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("reset", reset))

    logger.info("🤖 ATS Bot is running on Render!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
