"""
scanner.py — With debug mode to capture page HTML and screenshot
"""

import os
import json
import asyncio
import logging
import subprocess
import re
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

MASTER_SESSION_FILE = "master_session.json"
SKILLSYNCER_URL     = "https://skillsyncer.com"


def ensure_chromium():
    try:
        subprocess.run(["playwright", "install", "chromium"], capture_output=True, text=True, timeout=120)
        subprocess.run(["playwright", "install-deps", "chromium"], capture_output=True, text=True, timeout=120)
    except Exception as e:
        logger.error(f"Chromium install error: {e}")


class ATSScanner:

    async def scan(self, resume_text, jd_text, cookies=None, user_id=None):
        ensure_chromium()

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu", "--single-process"]
            )

            try:
                if os.path.exists(MASTER_SESSION_FILE):
                    context = await browser.new_context(
                        storage_state=MASTER_SESSION_FILE,
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                    )
                    logger.info("Using master session")
                else:
                    logger.info("No master session — logging in...")
                    context = await browser.new_context()
                    page = await context.new_page()
                    login_ok = await self._login_master(page)
                    if not login_ok:
                        return {"error": "Master login failed — check MASTER_EMAIL and MASTER_PASSWORD"}
                    await context.storage_state(path=MASTER_SESSION_FILE)

                page = await context.new_page()
                await page.goto(SKILLSYNCER_URL, wait_until="domcontentloaded")
                await asyncio.sleep(2)

                if "login" in page.url or "signin" in page.url:
                    if os.path.exists(MASTER_SESSION_FILE):
                        os.remove(MASTER_SESSION_FILE)
                    await browser.close()
                    return {"error": "Session expired — please try again"}

                result = await self._run_analysis(page, resume_text, jd_text)
                await browser.close()
                return result

            except Exception as e:
                logger.error(f"Scanner error: {e}")
                try: await browser.close()
                except: pass
                return {"error": str(e)}

    async def _login_master(self, page):
        email    = os.getenv("MASTER_EMAIL")
        password = os.getenv("MASTER_PASSWORD")
        if not email or not password:
            logger.error("MASTER_EMAIL/MASTER_PASSWORD not set")
            return False
        try:
            await page.goto(f"{SKILLSYNCER_URL}/login", wait_until="networkidle")
            await asyncio.sleep(1)
            await page.fill('input[type="email"], input[name="email"], #email', email)
            await asyncio.sleep(0.5)
            await page.fill('input[type="password"], input[name="password"], #password', password)
            await asyncio.sleep(0.5)
            await page.click('button[type="submit"]')
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)
            if "login" in page.url or "signin" in page.url:
                return False
            logger.info("Master login successful!")
            return True
        except Exception as e:
            logger.error(f"Login error: {e}")
            return False

    async def _run_analysis(self, page, resume_text, jd_text):
        try:
            await page.goto(f"{SKILLSYNCER_URL}/scanner", wait_until="networkidle")
            await asyncio.sleep(3)

            # Save pre-fill screenshot
            await page.screenshot(path="debug_before_fill.png", full_page=True)

            # Get all textareas on page for debugging
            textareas = await page.eval_on_selector_all(
                'textarea',
                'els => els.map(e => ({id: e.id, name: e.name, placeholder: e.placeholder, class: e.className}))'
            )
            logger.info(f"Textareas found: {json.dumps(textareas)}")

            # Fill resume
            filled_resume = False
            for sel in [
                'textarea[placeholder*="resume" i]',
                'textarea[name*="resume" i]',
                '#resume', 'textarea:first-of-type',
                'textarea',
            ]:
                try:
                    await page.fill(sel, resume_text, timeout=3000)
                    logger.info(f"Resume filled: {sel}")
                    filled_resume = True
                    break
                except: continue

            await asyncio.sleep(1)

            # Fill JD
            filled_jd = False
            for sel in [
                'textarea[placeholder*="job" i]',
                'textarea[name*="job" i]',
                '#job_description', '#jobDescription',
                'textarea:nth-of-type(2)', 'textarea:last-of-type',
            ]:
                try:
                    await page.fill(sel, jd_text, timeout=3000)
                    logger.info(f"JD filled: {sel}")
                    filled_jd = True
                    break
                except: continue

            await asyncio.sleep(1)

            # Get all buttons for debugging
            buttons = await page.eval_on_selector_all(
                'button',
                'els => els.map(e => ({text: e.textContent.trim(), type: e.type, class: e.className}))'
            )
            logger.info(f"Buttons found: {json.dumps(buttons)}")

            # Click analyze button
            for sel in [
                'button:has-text("Analyze")', 'button:has-text("Scan")',
                'button:has-text("Check")', 'button:has-text("Compare")',
                'button:has-text("Submit")', 'button[type="submit"]',
                '.analyze-btn', 'input[type="submit"]',
            ]:
                try:
                    await page.click(sel, timeout=3000)
                    logger.info(f"Clicked button: {sel}")
                    break
                except: continue

            # Wait for results
            await asyncio.sleep(10)
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(3)

            # Save post-result screenshot
            await page.screenshot(path="debug_after_scan.png", full_page=True)

            # Dump full page HTML for debugging
            html = await page.content()
            with open("debug_page.html", "w") as f:
                f.write(html)
            logger.info(f"Page HTML saved ({len(html)} chars)")

            # Try many selectors for score
            score = await self._extract_score(page)
            matched, missing = await self._extract_keywords(page)

            logger.info(f"Score={score}, matched={len(matched)}, missing={len(missing)}")
            return {"score": score, "matched_keywords": matched, "missing_keywords": missing}

        except Exception as e:
            logger.error(f"Analysis error: {e}")
            try: await page.screenshot(path="debug_error.png")
            except: pass
            return {"error": str(e)}

    async def _extract_score(self, page):
        # Try many possible selectors
        selectors = [
            '.score', '.score-circle', '.score-value', '.ats-score',
            '.match-score', '.percentage', '.job-match-score',
            '[class*="score"]', '[class*="match"]', '[class*="percent"]',
            'h1', 'h2', 'h3', 'h4',
            'span:has-text("%")', 'div:has-text("%")', 'p:has-text("%")',
        ]
        for sel in selectors:
            try:
                elements = await page.query_selector_all(sel)
                for el in elements:
                    text = await el.inner_text()
                    numbers = re.findall(r'\b(\d{1,3})\b', text)
                    for num in numbers:
                        if 1 <= int(num) <= 100 and '%' in text:
                            logger.info(f"Score found via '{sel}': {num}")
                            return num
            except: continue

        # Last resort — scan all page text for % patterns
        try:
            content = await page.content()
            matches = re.findall(r'(\d{1,3})%', content)
            valid = [m for m in matches if 1 <= int(m) <= 100]
            if valid:
                logger.info(f"Score from page text: {valid[0]}")
                return valid[0]
        except: pass

        return "N/A"

    async def _extract_keywords(self, page):
        matched, missing = [], []

        matched_selectors = [
            '.matched-keyword', '.keyword-found', '.keyword.found',
            '[class*="matched"]', '[class*="found"]', '.match',
            '.skill-matched', '.present',
        ]
        missing_selectors = [
            '.missing-keyword', '.keyword-missing', '.keyword.missing',
            '[class*="missing"]', '.skill-missing', '.absent',
        ]

        for sel in matched_selectors:
            try:
                items = await page.eval_on_selector_all(
                    sel, 'els => els.map(e => e.textContent.trim()).filter(t => t.length > 0 && t.length < 50)'
                )
                if items:
                    matched = items
                    logger.info(f"Matched via '{sel}': {items[:5]}")
                    break
            except: continue

        for sel in missing_selectors:
            try:
                items = await page.eval_on_selector_all(
                    sel, 'els => els.map(e => e.textContent.trim()).filter(t => t.length > 0 && t.length < 50)'
                )
                if items:
                    missing = items
                    logger.info(f"Missing via '{sel}': {items[:5]}")
                    break
            except: continue

        return matched[:20], missing[:20]