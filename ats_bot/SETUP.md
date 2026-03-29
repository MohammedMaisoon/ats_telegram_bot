# 🚀 Complete Render Deployment Guide
# ATS Score Telegram Bot — No Credit Card Needed

## Project Files
```
ats-telegram-bot/
├── bot.py            ← Main Telegram bot (with keep-alive)
├── scanner.py        ← Playwright SkillSyncer scraper
├── storage.py        ← Redis encrypted cookie storage
├── keep_alive.py     ← Flask server (prevents Render sleep)
├── generate_key.py   ← Run once to make encryption key
├── requirements.txt  ← Python packages
├── build.sh          ← Render build script
├── render.yaml       ← Render config
└── .env.example      ← Copy to .env and fill values
```

---

## STEP 1 — Create Telegram Bot (5 mins)

1. Open Telegram → search **@BotFather**
2. Send `/newbot`
3. Name: `ATS Score Checker`
4. Username: `ats_score_bot` (must be unique)
5. Copy the token → looks like:
   `7123456789:AAFxxxxxxxxxxxxxxxxxxxxx`

---

## STEP 2 — Get Free Redis (Upstash — No Card!)

Upstash gives free Redis with NO credit card needed.

1. Go to **upstash.com**
2. Sign up with Google (free)
3. Click **Create Database**
4. Name: `ats-bot-redis`
5. Region: pick closest to you
6. Click **Create**
7. Go to **Details** tab
8. Copy the **UPSTASH_REDIS_REST_URL** that starts with `rediss://`

---

## STEP 3 — Push Code to GitHub

```bash
# On your PC in the project folder:

git init
git add .
git commit -m "ATS Bot with Render keep-alive"

# Go to github.com → New Repository
# Name it: ats-telegram-bot
# Copy the repo URL then:

git remote add origin https://github.com/YOUR_USERNAME/ats-telegram-bot.git
git branch -M main
git push -u origin main
```

---

## STEP 4 — Deploy on Render (No Card!)

1. Go to **render.com**
2. Sign up with GitHub (free, no card)
3. Click **New → Web Service**
4. Click **Connect GitHub** → select `ats-telegram-bot` repo
5. Fill in settings:
   ```
   Name:            ats-telegram-bot
   Runtime:         Python 3
   Build Command:   bash build.sh
   Start Command:   python bot.py
   Plan:            Free
   ```
6. Click **Create Web Service**
7. Wait for build to finish (~3-5 minutes)
8. Copy your Render URL from top:
   `https://ats-telegram-bot.onrender.com`

---

## STEP 5 — Add Environment Variables on Render

Go to your service → **Environment** tab → Add these one by one:

```
BOT_TOKEN        → paste from BotFather
REDIS_URL        → paste from Upstash (rediss://...)
ENCRYPT_KEY      → run python generate_key.py locally, paste output
MASTER_EMAIL     → your SkillSyncer email
MASTER_PASSWORD  → your SkillSyncer password
RENDER_URL       → https://your-app-name.onrender.com
PORT             → 8080
```

Click **Save Changes** → Render auto-redeploys.

---

## STEP 6 — Verify Bot is Running

1. Go to Render → **Logs** tab
2. You should see:
   ```
   🌐 Flask server starting on port 8080
   ✅ Keep-alive system started!
   🤖 ATS Bot is running on Render!
   ```
3. Open Telegram → find your bot → send `/start`
4. Bot should respond instantly ✅

---

## How Keep-Alive Works

```
Bot starts on Render
      ↓
Flask server starts on port 8080
      ↓
Render thinks it's a web app (stays awake)
      ↓
Every 10 mins → bot pings its own URL
      ↓
Render sees traffic → never sleeps ✅
      ↓
Bot always responds instantly!
```

---

## How Cookies Are Stored in Cloud

```
User exports cookies from Chrome (~5KB JSON)
      ↓
Pastes in Telegram
      ↓
bot.py receives text
      ↓
storage.py encrypts with AES-256 (Fernet)
      ↓
Encrypted blob saved to Upstash Redis
Key: "cookies:USER_ID"
TTL: 30 days (auto-deletes!)
      ↓
Each scan:
  Redis → load encrypted blob
  Decrypt in RAM only
  Inject into Playwright
  Scan runs silently
  Browser closes → RAM cleared
  Encrypted blob stays in Redis
      ↓
After 30 days → Redis auto-deletes
Bot alerts user to re-send cookies
```

---

## Full Architecture

```
User Phone/PC
     │ Telegram
     ▼
Telegram Servers
     │
     ▼
┌──────────────────────────────────────┐
│           Render Cloud (Free)        │
│                                      │
│  ┌─────────────────────────────┐     │
│  │  bot.py (Telegram polling)  │     │
│  │  keep_alive.py (Flask :8080)│     │
│  │  scanner.py (Playwright)    │     │
│  └────────────┬────────────────┘     │
│               │ Redis URL            │
└───────────────┼──────────────────────┘
                │
                ▼
┌──────────────────────┐
│   Upstash Redis      │
│   (Free, No Card)    │
│                      │
│  cookies:USER1 ✅    │
│  cookies:USER2 ✅    │
│  temp:USER1:resume   │
└──────────────────────┘
                │
                │ Playwright
                ▼
        skillsyncer.com
                │
                ▼
        ATS Score → Telegram ✅
```

---

## Commands

| Command | What it does |
|---------|-------------|
| `/start` | Begin scan or welcome back |
| `/cancel` | Cancel current scan |
| `/reset` | Clear saved cookies, start fresh |

---

## Troubleshooting

### Bot not responding after deploy:
- Check Render Logs tab for errors
- Make sure BOT_TOKEN is correct in Environment tab

### Build failing:
- Check build.sh has correct permissions
- In Render logs look for pip/playwright errors

### Redis connection error:
- Double-check REDIS_URL starts with `rediss://` (with double s)
- Upstash free tier is `rediss://` not `redis://`

### Score showing N/A:
- SkillSyncer HTML may have changed
- Check `debug_screenshot.png` if saved
- Update selectors in scanner.py

### Cookies expired message:
- Normal after 30 days
- User re-exports from Cookie-Editor
- Pastes fresh cookies in bot
