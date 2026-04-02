"""
ATS Score Telegram Bot
Main bot file - handles all Telegram interactions
"""

import os
import io
import fitz  # PyMuPDF
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
from keep_alive import keep_alive

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

DONE = 2

# ── Storage & Scanner ────────────────────────────────────────
storage = RedisStorage()
scanner = ATSScanner()


# ════════════════════════════════════════════════════════════
#  /start
# ════════════════════════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    first_name = update.effective_user.first_name

    await update.message.reply_text(
        f"👋 Hello *{first_name}!* Welcome to *ATS Score Bot*\n\n"
        f"I'll check how well your resume matches a job description!\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📄 Send your *Resume* as a *PDF* or *TXT file*, or paste text in one message.\n"
        f"If your resume is long, send it as a file, or paste multiple chunks and finish with /done.",
        parse_mode="Markdown"
    )
    return WAITING_RESUME


# ════════════════════════════════════════════════════════════
#  Resume Handler — accepts PDF or text
# ════════════════════════════════════════════════════════════
async def receive_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    resume_text = ""

    if update.message.document:
        doc = update.message.document
        mime = doc.mime_type or ""

        # ── PDF ──────────────────────────────────────────────
        if mime == "application/pdf" or doc.file_name.endswith(".pdf"):
            if doc.file_size > 5_000_000:
                await update.message.reply_text("❌ PDF too large (max 5MB). Try a smaller file.")
                return WAITING_RESUME
            file = await doc.get_file()
            file_bytes = await file.download_as_bytearray()
            try:
                pdf = fitz.open(stream=bytes(file_bytes), filetype="pdf")
                for page in pdf:
                    resume_text += page.get_text()
                pdf.close()
            except Exception as e:
                await update.message.reply_text(f"❌ Could not read PDF. Try pasting as text.\nError: {e}")
                return WAITING_RESUME

        # ── Plain text file ──────────────────────────────────
        elif mime.startswith("text/") or doc.file_name.endswith(".txt"):
            if doc.file_size > 2_000_000:
                await update.message.reply_text("❌ File too large. Please paste resume as text or send a smaller .txt file.")
                return WAITING_RESUME
            file = await doc.get_file()
            file_bytes = await file.download_as_bytearray()
            resume_text = file_bytes.decode("utf-8", errors="ignore")

        else:
            await update.message.reply_text("❌ Please send a *PDF* file or paste resume as text.", parse_mode="Markdown")
            return WAITING_RESUME

    elif update.message.text:
        resume_text = update.message.text
    else:
        await update.message.reply_text("❌ Please send your resume as a PDF or text.")
        return WAITING_RESUME

    existing_resume = storage.get_temp(user_id, "resume") or ""
    combined_resume = f"{existing_resume}\n{resume_text}" if existing_resume else resume_text
    storage.save_temp(user_id, "resume", combined_resume)

    if existing_resume or len(combined_resume) > 2000:
        await update.message.reply_text(
            "✅ Resume part received. Send more text if needed, or send /done when finished.\n"
            "You can also send a PDF or TXT file as the next message."
        )
        return WAITING_RESUME

    if len(combined_resume.strip()) < 50:
        await update.message.reply_text("❌ Resume too short or empty. Please try again.")
        return WAITING_RESUME

    await update.message.reply_text(
        "✅ *Resume received!*\n\n"
        "📋 Now send the *Job Description* as a *PDF*/*TXT file* or paste text in one message.\n"
        "If the JD is long, send it as a file, or paste multiple chunks and finish with /done.",
        parse_mode="Markdown"
    )
    return WAITING_JD


# ════════════════════════════════════════════════════════════
#  JD Handler — Run the ATS scan!
# ════════════════════════════════════════════════════════════
async def _extract_text_from_message(message):
    content = ""
    if message.document:
        doc = message.document
        mime = doc.mime_type or ""
        if mime == "application/pdf" or doc.file_name.endswith(".pdf"):
            file = await doc.get_file()
            file_bytes = await file.download_as_bytearray()
            try:
                pdf = fitz.open(stream=bytes(file_bytes), filetype="pdf")
                for page in pdf:
                    content += page.get_text()
                pdf.close()
            except Exception as e:
                raise ValueError(f"Could not read PDF. Try sending text or a .txt file. Error: {e}")
        elif mime.startswith("text/") or doc.file_name.endswith(".txt"):
            if doc.file_size > 2_000_000:
                raise ValueError("Text file too large. Please send a smaller .txt file or paste the JD as text.")
            file = await doc.get_file()
            file_bytes = await file.download_as_bytearray()
            content = file_bytes.decode("utf-8", errors="ignore")
        else:
            raise ValueError("Please send a PDF or text file.")
    elif message.text:
        content = message.text
    return content


async def receive_jd_and_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        jd_text = await _extract_text_from_message(update.message)
    except ValueError as e:
        await update.message.reply_text(str(e))
        return WAITING_JD

    existing_jd = storage.get_temp(user_id, "jd") or ""
    combined_jd = f"{existing_jd}\n{jd_text}" if existing_jd else jd_text
    storage.save_temp(user_id, "jd", combined_jd)

    if existing_jd or len(combined_jd) > 2000:
        await update.message.reply_text(
            "✅ JD part received. Send more text if needed, or send /done when finished.\n"
            "You can also send a PDF or TXT file as the next message."
        )
        return WAITING_JD

    if len(combined_jd.strip()) < 50:
        await update.message.reply_text("❌ JD too short. Please send more of the job description.")
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
                f"❌ Something went wrong. Please try /start again.\nError: {str(error)[:100]}"
            )
            return ConversationHandler.END

        try:
            score_int = int(str(score).replace("%", "").strip())
            if score_int >= 75:
                emoji, verdict = "🟢", "Excellent Match!"
            elif score_int >= 55:
                emoji, verdict = "🟡", "Good Match"
            elif score_int >= 35:
                emoji, verdict = "🟠", "Partial Match"
            else:
                emoji, verdict = "🔴", "Poor Match"
        except:
            emoji, verdict = "📊", "Score Retrieved"

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
            f"❌ Something went wrong. Please try /start again.\nError: {str(e)[:100]}"
        )

    return ConversationHandler.END


async def _execute_scan(update: Update, resume_text: str, jd_text: str):
    user_id = update.effective_user.id
    status_msg = await update.message.reply_text(
        "⏳ *Checking your ATS score...*\n\n"
        "🔄 Running analysis (30-60 seconds)\n"
        "Please wait...",
        parse_mode="Markdown"
    )

    try:
        result = await scanner.scan(
            resume_text=resume_text,
            jd_text=jd_text,
            cookies=None,
            user_id=user_id
        )

        storage.delete_temp(user_id, "resume")
        storage.delete_temp(user_id, "jd")

        score = result.get("score", "N/A")
        matched = result.get("matched_keywords", [])
        missing = result.get("missing_keywords", [])
        error = result.get("error")

        if error:
            await status_msg.edit_text(
                f"❌ Something went wrong. Please try /start again.\nError: {str(error)[:100]}"
            )
            return ConversationHandler.END

        try:
            score_int = int(str(score).replace("%", "").strip())
            if score_int >= 75:
                emoji, verdict = "🟢", "Excellent Match!"
            elif score_int >= 55:
                emoji, verdict = "🟡", "Good Match"
            elif score_int >= 35:
                emoji, verdict = "🟠", "Partial Match"
            else:
                emoji, verdict = "🔴", "Poor Match"
        except:
            emoji, verdict = "📊", "Score Retrieved"

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
            f"❌ Something went wrong. Please try /start again.\nError: {str(e)[:100]}"
        )

    return ConversationHandler.END


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    resume_text = storage.get_temp(user_id, "resume")
    jd_text = storage.get_temp(user_id, "jd")

    if resume_text and not jd_text:
        if len(resume_text.strip()) < 50:
            await update.message.reply_text("❌ Resume too short. Send more text or a file.")
            return WAITING_RESUME

        await update.message.reply_text(
            "✅ Resume complete. Now send the Job Description as text or file, or send /done when finished."
        )
        return WAITING_JD

    if resume_text and jd_text:
        if len(jd_text.strip()) < 50:
            await update.message.reply_text("❌ JD too short. Send more text or a file.")
            return WAITING_JD
        return await _execute_scan(update, resume_text, jd_text)

    await update.message.reply_text(
        "❌ I don't have enough data yet. Send your resume or JD first."
    )
    return WAITING_RESUME


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
    keep_alive()

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAITING_RESUME: [
                MessageHandler(
                    (filters.TEXT | filters.Document.ALL) & ~filters.COMMAND,
                    receive_resume
                ),
                CommandHandler("done", done)
            ],
            WAITING_JD: [
                MessageHandler(
                    (filters.TEXT | filters.Document.ALL) & ~filters.COMMAND,
                    receive_jd_and_scan
                ),
                CommandHandler("done", done)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
            CommandHandler("done", done),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)

    logger.info("🤖 ATS Bot is running!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()