#!/usr/bin/env bash
# build.sh — Render runs this during build phase
set -e

echo "📦 Installing Python dependencies..."
pip install -r ats_bot/requirements.txt

echo "🌐 Installing Playwright + Chromium..."
playwright install chromium
playwright install-deps chromium

echo "✅ Build complete!"
