#!/usr/bin/env bash
# build.sh — Railway/Render runs this during build phase
set -e

echo "📦 Installing Python dependencies..."
pip install -r requirements.txt

echo "🌐 Installing Playwright system dependencies..."
playwright install-deps chromium

echo "🌐 Installing Playwright Chromium browser..."
playwright install chromium

echo "✅ Build complete!"