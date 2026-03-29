import asyncio
import logging
import os
import re
import subprocess
import sys

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

MASTER_EMAIL = os.environ.get("MASTER_EMAIL", "")
MASTER_PASSWORD = os.environ.get("MASTER_PASSWORD", "")

# Shared session storage
_master_context = None
_playwright_instance = None
_browser_instance = None


def _ensure_chromium():
    """Install Playwright Chromium if not already installed."""
    try:
        result = subprocess.run(
            ["python", "-m", "playwright", "install", "chromium"],
            capture_output=True, text=True, timeout=120
        )
        logger.info(f"Playwright install: {result.stdout[-200:] if result.stdout else 'done'}")
    except Exception as e:
        logger.warning(f"Playwright install warning: {e}")


async def _get_browser():
    """Get or create a shared browser instance."""
    global _playwright_instance, _browser_instance
    if _browser_instance is None:
        _ensure_chromium()
        _playwright_instance = await async_playwright().start()
        _browser_instance = await _playwright_instance.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ]
        )
        logger.info("Browser launched.")
    return _browser_instance


async def _login_master(context):
    """Log in to SkillSyncer with master credentials."""
    page = await context.new_page()
    try:
        logger.info("Logging in to SkillSyncer...")
        await page.goto("https://skillsyncer.com/login", wait_until="domcontentloaded", timeout=30000)
        await page.get_by_role("textbox", name="you@mail.com").fill(MASTER_EMAIL)
        await page.get_by_role("textbox", name="password").fill(MASTER_PASSWORD)
        await page.get_by_role("button", name="Sign In").click()
        await page.wait_for_url("**/dashboard", timeout=20000)
        logger.info("Master login successful!")
        return True
    except Exception as e:
        logger.error(f"Login failed: {e}")
        return False
    finally:
        await page.close()


async def _check_scans_remaining(context):
    """Check if scans are remaining on this account."""
    page = await context.new_page()
    try:
        await page.goto("https://app.skillsyncer.com/dashboard", wait_until="domcontentloaded", timeout=20000)
        content = await page.content()
        # Look for "Scans Left:" followed by a number
        match = re.search(r'Scans Left.*?(\d+)', content)
        if match:
            scans = int(match.group(1))
            logger.info(f"Scans remaining: {scans}")
            return scans
        return 0
    except Exception as e:
        logger.error(f"Could not check scans: {e}")
        return 0
    finally:
        await page.close()


async def get_ats_score(resume_text: str, job_description: str) -> dict:
    """
    Submit resume + JD to SkillSyncer and return ATS score results.
    Uses the master account. Returns dict with score, matched, missing keywords.
    """
    global _master_context

    try:
        browser = await _get_browser()

        # Create or reuse session
        if _master_context is None:
            logger.info("No master session — logging in...")
            _master_context = await browser.new_context()
            success = await _login_master(_master_context)
            if not success:
                _master_context = None
                return {"error": "Login failed. Check MASTER_EMAIL and MASTER_PASSWORD."}
        else:
            logger.info("Using master session")

        # Check scans remaining
        scans_left = await _check_scans_remaining(_master_context)
        if scans_left == 0:
            logger.warning("No scans left on master account!")
            return {"error": "No scans left on SkillSyncer account. Resets every Sunday."}

        # Open new scan
        page = await _master_context.new_page()
        try:
            await page.goto("https://app.skillsyncer.com/dashboard", wait_until="domcontentloaded", timeout=20000)

            # Click "New Scan" button to open modal
            logger.info("Opening New Scan modal...")
            await page.get_by_role("button", name="New Scan").click()

            # Wait for modal to appear — "Create New Scan" heading confirms it's open
            await page.get_by_role("heading", name="Create New Scan").wait_for(timeout=8000)
            logger.info("Modal opened!")

            # Fill Job Description — it's a contenteditable textbox (rich text editor)
            # ref=e1301 in our inspection: textbox inside the Job Description section
            jd_box = page.locator('div[contenteditable="true"]').nth(0)
            await jd_box.click()
            await jd_box.fill(job_description)
            logger.info("Job description filled.")

            # Fill Resume — second contenteditable textbox
            resume_box = page.locator('div[contenteditable="true"]').nth(1)
            await resume_box.click()
            await resume_box.fill(resume_text)
            logger.info("Resume filled.")

            # Click "Scan" button
            await page.get_by_role("button", name="Scan").click()
            logger.info("Scan submitted, waiting for results...")

            # Wait for navigation to scan result page (/scans/<uuid>)
            await page.wait_for_url("**/scans/**", timeout=60000)
            logger.info(f"Results page: {page.url}")

            # Wait for score to appear
            await asyncio.sleep(3)

            # Extract the score — it appears as a large number in the match report section
            content = await page.content()

            # Primary: look for the score percentage in the match report
            # The score appears as e.g. <generic ref=...>11</generic> inside the match report
            score = None

            # Try to get score from page text using regex
            # SkillSyncer shows score like: "11" inside a circle in the match report
            # We look for the pattern that appears near "Match" text
            score_matches = re.findall(r'\b(\d{1,3})\b', content)

            # Get the page text cleanly
            page_text = await page.evaluate("() => document.body.innerText")

            # Look for score near "computed match rate" or "match rate is"
            score_pattern = re.search(
                r'computed match rate is[^\d]*(\d+)|'
                r'Your match score[^\d]*(\d+)|'
                r'Match Report[\s\S]{0,200}?(\d+)%',
                page_text, re.IGNORECASE
            )
            if score_pattern:
                score = int(next(g for g in score_pattern.groups() if g is not None))
                logger.info(f"Score from match report text: {score}")
            else:
                # Fallback: find score from the circular display
                # It's typically the first standalone 1-3 digit number in the score section
                numbers_in_page = re.findall(r'\b(\d{1,3})\b', page_text[:3000])
                for n in numbers_in_page:
                    n_int = int(n)
                    if 0 <= n_int <= 100:
                        score = n_int
                        logger.info(f"Score from page text fallback: {score}")
                        break

            # Extract matched and missing keywords from the keyword table
            matched_keywords = []
            missing_keywords = []

            # Parse keyword rows: "Keyword Type Score Resume Job"
            # Matched = Score 100% (resume count > 0), Missing = Score 0%
            keyword_rows = re.findall(
                r'([A-Za-z][A-Za-z\s]{1,40}?)\s+(Hard|Soft|Other)\s+(\d+)%\s+(\d+)\s+(\d+)',
                page_text
            )
            for row in keyword_rows:
                keyword, ktype, score_pct, resume_count, job_count = row
                keyword = keyword.strip()
                if int(score_pct) > 0:
                    matched_keywords.append(keyword)
                else:
                    missing_keywords.append(keyword)

            logger.info(f"Score={score}, matched={len(matched_keywords)}, missing={len(missing_keywords)}")

            if score is None:
                score = 0

            return {
                "score": score,
                "matched": matched_keywords,
                "missing": missing_keywords,
                "scan_url": page.url
            }

        finally:
            await page.close()

    except Exception as e:
        logger.error(f"Scanner error: {e}", exc_info=True)
        # Reset session on error so next call re-logs in
        _master_context = None
        return {"error": str(e)}