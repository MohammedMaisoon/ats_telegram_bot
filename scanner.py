"""
scanner.py — Fixed for SkillSyncer's modal-based form
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

            # ── Step 1: Click "Score My Resume" to open the modal ──
            logger.info("Clicking 'Score My Resume' button to open form...")
            try:
                await page.click('button:has-text("Score My Resume")', timeout=5000)
                await asyncio.sleep(2)
                logger.info("Clicked Score My Resume button")
            except Exception as e:
                logger.warning(f"Could not click Score My Resume: {e}")

            # ── Step 2: Close any popup/modal that appeared first ──
            # Sometimes there's a welcome modal — close it
            try:
                await page.click('button.close', timeout=2000)
                await asyncio.sleep(1)
                logger.info("Closed popup")
            except:
                pass

            # ── Step 3: Check what textareas are visible now ──
            textareas = await page.eval_on_selector_all(
                'textarea',
                'els => els.map(e => ({id: e.id, name: e.name, placeholder: e.placeholder, class: e.className, visible: e.offsetParent !== null}))'
            )
            logger.info(f"Textareas after button click: {json.dumps(textareas)}")

            await page.screenshot(path="debug_after_button.png", full_page=True)

            # ── Step 4: Fill resume textarea ──
            filled_resume = False
            resume_selectors = [
                'textarea[placeholder*="resume" i]',
                'textarea[placeholder*="paste" i]',
                'textarea[name*="resume" i]',
                '#resume', '#resumeText', '#resume_text',
                '.modal textarea:first-of-type',
                'textarea:first-of-type',
                'textarea',
            ]
            for sel in resume_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        visible = await el.is_visible()
                        if visible:
                            await el.fill(resume_text)
                            logger.info(f"Resume filled with: {sel}")
                            filled_resume = True
                            break
                except:
                    continue

            if not filled_resume:
                logger.warning("Could not fill resume!")

            await asyncio.sleep(1)

            # ── Step 5: Fill JD textarea ──
            filled_jd = False
            jd_selectors = [
                'textarea[placeholder*="job" i]',
                'textarea[placeholder*="description" i]',
                'textarea[name*="job" i]',
                '#job_description', '#jobDescription', '#jd',
                '.modal textarea:last-of-type',
                'textarea:nth-of-type(2)',
                'textarea:last-of-type',
            ]
            for sel in jd_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        visible = await el.is_visible()
                        if visible:
                            await el.fill(jd_text)
                            logger.info(f"JD filled with: {sel}")
                            filled_jd = True
                            break
                except:
                    continue

            if not filled_jd:
                logger.warning("Could not fill JD!")

            await asyncio.sleep(1)
            await page.screenshot(path="debug_after_fill.png", full_page=True)

            # ── Step 6: Submit the form ──
            submit_selectors = [
                'button[type="submit"]',
                'button:has-text("Analyze")',
                'button:has-text("Scan")',
                'button:has-text("Check")',
                'button:has-text("Score")',
                'button:has-text("Submit")',
                'input[type="submit"]',
            ]
            for sel in submit_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        visible = await el.is_visible()
                        if visible:
                            await el.click()
                            logger.info(f"Clicked submit: {sel}")
                            break
                except:
                    continue

            # ── Step 7: Wait for results ──
            logger.info("Waiting for results...")
            await asyncio.sleep(15)
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(3)

            await page.screenshot(path="debug_results.png", full_page=True)

            # Save HTML for inspection
            html = await page.content()
            with open("debug_page.html", "w") as f:
                f.write(html)
            logger.info(f"Results page HTML saved ({len(html)} chars)")

            # ── Step 8: Extract score and keywords ──
            score = await self._extract_score(page)
            matched, missing = await self._extract_keywords(page)

            logger.info(f"Final: Score={score}, matched={len(matched)}, missing={len(missing)}")
            return {"score": score, "matched_keywords": matched, "missing_keywords": missing}

        except Exception as e:
            logger.error(f"Analysis error: {e}")
            try: await page.screenshot(path="debug_error.png")
            except: pass
            return {"error": str(e)}

    async def _extract_score(self, page):
        # Try specific score selectors first
        specific_selectors = [
            '.score-circle', '.score-value', '.ats-score',
            '.match-score', '.job-match', '.result-score',
            '[class*="score"]', '[class*="result"]', '[class*="match"]',
            'h1', 'h2', 'h3',
        ]
        for sel in specific_selectors:
            try:
                elements = await page.query_selector_all(sel)
                for el in elements:
                    if await el.is_visible():
                        text = await el.inner_text()
                        text = text.strip()
                        if '%' in text:
                            nums = re.findall(r'\b(\d{1,3})\b', text)
                            for num in nums:
                                if 1 <= int(num) <= 100:
                                    logger.info(f"Score via '{sel}': {num}")
                                    return num
            except:
                continue

        # Fallback: scan page text
        try:
            content = await page.content()
            matches = re.findall(r'(\d{1,3})%', content)
            valid = [m for m in matches if 1 <= int(m) <= 100]
            if valid:
                logger.info(f"Score from page text: {valid[0]}, all found: {valid[:5]}")
                return valid[0]
        except:
            pass

        return "N/A"

    async def _extract_keywords(self, page):
        matched, missing = [], []

        for sel in ['.matched-keyword', '.keyword-found', '[class*="matched"]', '[class*="found"]', '.skill-matched']:
            try:
                items = await page.eval_on_selector_all(
                    sel, 'els => els.filter(e => e.offsetParent !== null).map(e => e.textContent.trim()).filter(t => t.length > 0 && t.length < 50)'
                )
                if items:
                    matched = items
                    logger.info(f"Matched keywords via '{sel}': {items[:5]}")
                    break
            except:
                continue

        for sel in ['.missing-keyword', '.keyword-missing', '[class*="missing"]', '.skill-missing']:
            try:
                items = await page.eval_on_selector_all(
                    sel, 'els => els.filter(e => e.offsetParent !== null).map(e => e.textContent.trim()).filter(t => t.length > 0 && t.length < 50)'
                )
                if items:
                    missing = items
                    logger.info(f"Missing keywords via '{sel}': {items[:5]}")
                    break
            except:
                continue

        return matched[:20], missing[:20]