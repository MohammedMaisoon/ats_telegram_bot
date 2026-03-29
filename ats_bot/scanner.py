"""
scanner.py — Playwright-based ATS scanner using SkillSyncer
Runs headless Chromium, injects user cookies, extracts score silently
"""

import os
import json
import asyncio
import logging
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

logger = logging.getLogger(__name__)

MASTER_SESSION_FILE = "master_session.json"  # Saved after master login
SKILLSYNCER_URL     = "https://skillsyncer.com"


class ATSScanner:

    async def scan(
        self,
        resume_text: str,
        jd_text: str,
        cookies: list = None,
        user_id: int = None,
    ) -> dict:
        """
        Main scan entry point.
        - cookies=None  → uses master session file
        - cookies=[...] → injects user's own cookies
        Returns dict with score, matched_keywords, missing_keywords
        """
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )

            try:
                # ── Build browser context ────────────────────
                if cookies:
                    # User's own cookies
                    context = await browser.new_context(
                        user_agent=(
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        )
                    )
                    await context.add_cookies(cookies)
                    logger.info(f"Using user cookies for {user_id}")

                elif os.path.exists(MASTER_SESSION_FILE):
                    # Master account saved session
                    context = await browser.new_context(
                        storage_state=MASTER_SESSION_FILE,
                        user_agent=(
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        )
                    )
                    logger.info("Using master session")

                else:
                    # No session — do master login first
                    logger.info("No master session found — logging in...")
                    context = await browser.new_context()
                    page = await context.new_page()
                    login_ok = await self._login_master(page)
                    if not login_ok:
                        return {"error": "Master login failed"}
                    # Save session for future use
                    await context.storage_state(path=MASTER_SESSION_FILE)
                    logger.info("Master session saved!")

                page = await context.new_page()

                # ── Check if session is valid ────────────────
                await page.goto(SKILLSYNCER_URL, wait_until="domcontentloaded")
                await asyncio.sleep(2)

                if "login" in page.url or "signin" in page.url:
                    logger.warning(f"Session expired for user {user_id}")
                    await browser.close()
                    return {"error": "session_expired"}

                # ── Navigate to the analyzer ─────────────────
                result = await self._run_analysis(page, resume_text, jd_text)
                await browser.close()
                return result

            except Exception as e:
                logger.error(f"Scanner error: {e}")
                await browser.close()
                return {"error": str(e)}

    # ════════════════════════════════════════════════════════
    #  Master Login (one time only)
    # ════════════════════════════════════════════════════════
    async def _login_master(self, page) -> bool:
        email    = os.getenv("MASTER_EMAIL")
        password = os.getenv("MASTER_PASSWORD")

        if not email or not password:
            logger.error("MASTER_EMAIL/MASTER_PASSWORD not set in .env")
            return False

        try:
            await page.goto(f"{SKILLSYNCER_URL}/login", wait_until="networkidle")
            await asyncio.sleep(1)

            # Fill login form
            await page.fill('input[type="email"], input[name="email"], #email', email)
            await asyncio.sleep(0.5)
            await page.fill('input[type="password"], input[name="password"], #password', password)
            await asyncio.sleep(0.5)
            await page.click('button[type="submit"]')
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)

            # Check login success
            if "login" in page.url or "signin" in page.url:
                logger.error("Master login failed — check credentials")
                return False

            logger.info("Master login successful!")
            return True

        except Exception as e:
            logger.error(f"Login error: {e}")
            return False

    # ════════════════════════════════════════════════════════
    #  Run SkillSyncer Analysis
    # ════════════════════════════════════════════════════════
    async def _run_analysis(self, page, resume_text: str, jd_text: str) -> dict:
        try:
            # Go to the analysis/scanner page
            await page.goto(f"{SKILLSYNCER_URL}/scanner", wait_until="networkidle")
            await asyncio.sleep(2)

            # ── Fill Resume ──────────────────────────────────
            resume_selectors = [
                'textarea[placeholder*="resume" i]',
                'textarea[name*="resume" i]',
                '#resume',
                '.resume-input textarea',
                'textarea:first-of-type',
            ]
            for sel in resume_selectors:
                try:
                    await page.fill(sel, resume_text, timeout=3000)
                    logger.info(f"Resume filled with selector: {sel}")
                    break
                except:
                    continue

            await asyncio.sleep(1)

            # ── Fill Job Description ─────────────────────────
            jd_selectors = [
                'textarea[placeholder*="job" i]',
                'textarea[name*="job" i]',
                '#job_description',
                '#jobDescription',
                '.job-input textarea',
                'textarea:last-of-type',
                'textarea:nth-of-type(2)',
            ]
            for sel in jd_selectors:
                try:
                    await page.fill(sel, jd_text, timeout=3000)
                    logger.info(f"JD filled with selector: {sel}")
                    break
                except:
                    continue

            await asyncio.sleep(1)

            # ── Click Analyze Button ─────────────────────────
            btn_selectors = [
                'button:has-text("Analyze")',
                'button:has-text("Scan")',
                'button:has-text("Check")',
                'button:has-text("Compare")',
                'button[type="submit"]',
                '.analyze-btn',
            ]
            for sel in btn_selectors:
                try:
                    await page.click(sel, timeout=3000)
                    logger.info(f"Clicked button: {sel}")
                    break
                except:
                    continue

            # Wait for results
            await asyncio.sleep(8)
            await page.wait_for_load_state("networkidle")

            # ── Extract Score ────────────────────────────────
            score = await self._extract_score(page)
            matched, missing = await self._extract_keywords(page)

            logger.info(f"Scan complete — Score: {score}")
            return {
                "score": score,
                "matched_keywords": matched,
                "missing_keywords": missing,
            }

        except Exception as e:
            logger.error(f"Analysis error: {e}")
            # Take screenshot for debugging
            try:
                await page.screenshot(path=f"debug_screenshot.png")
                logger.info("Debug screenshot saved")
            except:
                pass
            return {"error": str(e)}

    # ════════════════════════════════════════════════════════
    #  Extract Score from Page
    # ════════════════════════════════════════════════════════
    async def _extract_score(self, page) -> str:
        score_selectors = [
            '.score-circle',
            '.score-value',
            '.ats-score',
            '[class*="score"]',
            '.match-score',
            '.percentage',
            'h2:has-text("%")',
            'span:has-text("%")',
        ]
        for sel in score_selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    text = await el.inner_text()
                    # Extract number from text
                    import re
                    numbers = re.findall(r'\d+', text)
                    if numbers:
                        return numbers[0]
            except:
                continue

        # Fallback: search entire page text for percentage
        try:
            import re
            content = await page.content()
            matches = re.findall(r'(\d{1,3})%', content)
            if matches:
                return matches[0]
        except:
            pass

        return "N/A"

    # ════════════════════════════════════════════════════════
    #  Extract Keywords from Page
    # ════════════════════════════════════════════════════════
    async def _extract_keywords(self, page):
        matched = []
        missing = []

        try:
            matched = await page.eval_on_selector_all(
                '.matched-keyword, .keyword-found, [class*="matched"], .keyword.found',
                'els => els.map(e => e.textContent.trim()).filter(t => t.length > 0)'
            )
        except:
            pass

        try:
            missing = await page.eval_on_selector_all(
                '.missing-keyword, .keyword-missing, [class*="missing"], .keyword.missing',
                'els => els.map(e => e.textContent.trim()).filter(t => t.length > 0)'
            )
        except:
            pass

        return matched[:20], missing[:20]
