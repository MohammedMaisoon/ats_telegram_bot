"""
scanner.py — Playwright-based ATS scanner using SkillSyncer
Installs Chromium at runtime if not found
"""

import os
import json
import asyncio
import logging
import subprocess
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

MASTER_SESSION_FILE = "master_session.json"
SKILLSYNCER_URL     = "https://skillsyncer.com"


def ensure_chromium():
    """Install Playwright Chromium at runtime if missing."""
    try:
        result = subprocess.run(
            ["playwright", "install", "chromium"],
            capture_output=True, text=True, timeout=120
        )
        logger.info(f"Playwright install: {result.stdout}")
        # Also install deps
        subprocess.run(
            ["playwright", "install-deps", "chromium"],
            capture_output=True, text=True, timeout=120
        )
    except Exception as e:
        logger.error(f"Chromium install error: {e}")


class ATSScanner:

    async def scan(
        self,
        resume_text: str,
        jd_text: str,
        cookies: list = None,
        user_id: int = None,
    ) -> dict:

        # Install Chromium if not present
        ensure_chromium()

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--single-process",
                ]
            )

            try:
                if cookies:
                    context = await browser.new_context(
                        user_agent=(
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        )
                    )
                    await context.add_cookies(cookies)

                elif os.path.exists(MASTER_SESSION_FILE):
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
                    logger.info("No master session — logging in...")
                    context = await browser.new_context()
                    page = await context.new_page()
                    login_ok = await self._login_master(page)
                    if not login_ok:
                        return {"error": "Master login failed — check MASTER_EMAIL and MASTER_PASSWORD in Railway variables"}
                    await context.storage_state(path=MASTER_SESSION_FILE)
                    logger.info("Master session saved!")

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
                try:
                    await browser.close()
                except:
                    pass
                return {"error": str(e)}

    async def _login_master(self, page) -> bool:
        email    = os.getenv("MASTER_EMAIL")
        password = os.getenv("MASTER_PASSWORD")

        if not email or not password:
            logger.error("MASTER_EMAIL/MASTER_PASSWORD not set in env")
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
                logger.error("Master login failed — check credentials")
                return False

            logger.info("Master login successful!")
            return True

        except Exception as e:
            logger.error(f"Login error: {e}")
            return False

    async def _run_analysis(self, page, resume_text: str, jd_text: str) -> dict:
        try:
            await page.goto(f"{SKILLSYNCER_URL}/scanner", wait_until="networkidle")
            await asyncio.sleep(2)

            for sel in [
                'textarea[placeholder*="resume" i]',
                'textarea[name*="resume" i]',
                '#resume', '.resume-input textarea', 'textarea:first-of-type',
            ]:
                try:
                    await page.fill(sel, resume_text, timeout=3000)
                    logger.info(f"Resume filled: {sel}")
                    break
                except:
                    continue

            await asyncio.sleep(1)

            for sel in [
                'textarea[placeholder*="job" i]',
                'textarea[name*="job" i]',
                '#job_description', '#jobDescription',
                '.job-input textarea', 'textarea:last-of-type', 'textarea:nth-of-type(2)',
            ]:
                try:
                    await page.fill(sel, jd_text, timeout=3000)
                    logger.info(f"JD filled: {sel}")
                    break
                except:
                    continue

            await asyncio.sleep(1)

            for sel in [
                'button:has-text("Analyze")', 'button:has-text("Scan")',
                'button:has-text("Check")', 'button:has-text("Compare")',
                'button[type="submit"]', '.analyze-btn',
            ]:
                try:
                    await page.click(sel, timeout=3000)
                    logger.info(f"Clicked: {sel}")
                    break
                except:
                    continue

            await asyncio.sleep(8)
            await page.wait_for_load_state("networkidle")

            score = await self._extract_score(page)
            matched, missing = await self._extract_keywords(page)

            return {"score": score, "matched_keywords": matched, "missing_keywords": missing}

        except Exception as e:
            logger.error(f"Analysis error: {e}")
            try:
                await page.screenshot(path="debug_screenshot.png")
            except:
                pass
            return {"error": str(e)}

    async def _extract_score(self, page) -> str:
        for sel in [
            '.score-circle', '.score-value', '.ats-score',
            '[class*="score"]', '.match-score', '.percentage',
            'h2:has-text("%")', 'span:has-text("%")',
        ]:
            try:
                el = await page.query_selector(sel)
                if el:
                    text = await el.inner_text()
                    import re
                    numbers = re.findall(r'\d+', text)
                    if numbers:
                        return numbers[0]
            except:
                continue
        try:
            import re
            content = await page.content()
            matches = re.findall(r'(\d{1,3})%', content)
            if matches:
                return matches[0]
        except:
            pass
        return "N/A"

    async def _extract_keywords(self, page):
        matched, missing = [], []
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