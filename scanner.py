"""
ATS Scanner — uses SkillSyncer via Playwright browser automation.
Exposes ATSScanner class with async scan() method to match bot.py usage.
"""

import asyncio
import logging
import os
import re
import shutil

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

MASTER_EMAIL    = os.environ.get("MASTER_EMAIL", "")
MASTER_PASSWORD = os.environ.get("MASTER_PASSWORD", "")

# System Chromium installed by apt-get in Dockerfile
CHROMIUM_PATHS = [
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
]

def _find_chromium() -> str | None:
    """Return path to system Chromium binary."""
    for path in CHROMIUM_PATHS:
        if os.path.exists(path):
            logger.info(f"Found system Chromium: {path}")
            return path
    # fallback: search PATH
    found = shutil.which("chromium") or shutil.which("chromium-browser")
    if found:
        logger.info(f"Found Chromium via PATH: {found}")
        return found
    logger.warning("No system Chromium found — letting Playwright use its own.")
    return None


class ATSScanner:
    """
    Singleton-style scanner. Reuses one browser + logged-in session
    across all Telegram users.
    """

    def __init__(self):
        self._playwright = None
        self._browser    = None
        self._context    = None
        self._lock       = asyncio.Lock()

    async def _start_browser(self):
        if self._playwright is None:
            self._playwright = await async_playwright().start()

        if self._browser is None or not self._browser.is_connected():
            chromium_path = _find_chromium()
            launch_kwargs = dict(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--single-process",
                    "--no-zygote",
                ]
            )
            if chromium_path:
                launch_kwargs["executable_path"] = chromium_path

            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
            logger.info("Browser launched successfully.")

    async def _login(self):
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

        await self._start_browser()
        self._context = await self._browser.new_context()
        page = await self._context.new_page()
        try:
            logger.info("Logging in to SkillSyncer...")
            await page.goto(
                "https://skillsyncer.com/login",
                wait_until="domcontentloaded",
                timeout=30000
            )
            await page.get_by_role("textbox", name="you@mail.com").fill(MASTER_EMAIL)
            await page.get_by_role("textbox", name="password").fill(MASTER_PASSWORD)
            await page.get_by_role("button", name="Sign In").click()
            await page.wait_for_url("**/dashboard", timeout=20000)
            logger.info("Master login successful!")
            return True
        except Exception as e:
            logger.error(f"Login failed: {e}")
            self._context = None
            return False
        finally:
            await page.close()

    async def _scans_remaining(self) -> int:
        page = await self._context.new_page()
        try:
            await page.goto(
                "https://app.skillsyncer.com/dashboard",
                wait_until="domcontentloaded",
                timeout=20000
            )
            text = await page.evaluate("() => document.body.innerText")
            match = re.search(r'Scans Left[:\s]+(\d+)', text)
            count = int(match.group(1)) if match else 1
            logger.info(f"Scans remaining: {count}")
            return count
        except Exception as e:
            logger.warning(f"Could not read scan count: {e}")
            return 1
        finally:
            await page.close()

    async def scan(self, resume_text: str, jd_text: str,
                   cookies=None, user_id=None) -> dict:
        async with self._lock:
            return await self._do_scan(resume_text, jd_text)

    async def _do_scan(self, resume_text: str, jd_text: str) -> dict:
        # Ensure we have a logged-in session
        if self._context is None:
            logger.info("No session — logging in...")
            ok = await self._login()
            if not ok:
                return {
                    "score": 0, "matched_keywords": [], "missing_keywords": [],
                    "error": "Login failed. Check MASTER_EMAIL / MASTER_PASSWORD in Railway."
                }
        else:
            logger.info("Reusing existing session.")

        # Check scans remaining
        remaining = await self._scans_remaining()
        if remaining == 0:
            return {
                "score": 0, "matched_keywords": [], "missing_keywords": [],
                "error": "No scans left on SkillSyncer (free plan: 2/week). Resets every Sunday."
            }

        page = await self._context.new_page()
        try:
            await page.goto(
                "https://app.skillsyncer.com/dashboard",
                wait_until="domcontentloaded",
                timeout=20000
            )

            # Open New Scan modal — try multiple selectors
            logger.info("Clicking New Scan button...")
            await page.wait_for_load_state("networkidle", timeout=10000)
            await page.screenshot(path="/tmp/dashboard.png")
            html = await page.content()
            logger.info(f"PAGE HTML SNIPPET: {html[2000:4000]}")


            # Try all known button selectors
            clicked = False
            for selector in [
                "button[name='New Scan']",
                "button:has-text('New Scan')",
                "a:has-text('New Scan')",
                "[data-testid='new-scan']",
                "button.btn:has-text('Scan')",
                "text=New Scan",
            ]:
                try:
                    el = page.locator(selector).first
                    if await el.is_visible(timeout=2000):
                        await el.click()
                        clicked = True
                        logger.info(f"Clicked using selector: {selector}")
                        break
                except Exception:
                    continue

            if not clicked:
                raise Exception("Could not find New Scan button — check /tmp/dashboard.png screenshot")

            await asyncio.sleep(4)  # Wait for modal animation
            await page.screenshot(path="/tmp/modal.png")
            logger.info("Modal screenshot saved.")

            # Fill Job Description — first contenteditable div
            jd_box = page.locator('div[contenteditable="true"]').nth(0)
            await jd_box.click()
            await page.keyboard.type(jd_text, delay=10)
            logger.info("JD filled.")

            # Fill Resume — second contenteditable div
            resume_box = page.locator('div[contenteditable="true"]').nth(1)
            await resume_box.click()
            await page.keyboard.type(resume_text, delay=10)
            logger.info("Resume filled.")

            # Click Scan
            await page.get_by_role("button", name="Scan").click()
            logger.info("Scan submitted — waiting for results page...")

            # Wait for /scans/<uuid>
            await page.wait_for_url("**/scans/**", timeout=60000)
            await asyncio.sleep(3)
            logger.info(f"Results at: {page.url}")

            page_text = await page.evaluate("() => document.body.innerText")

            # Extract score
            score = None
            m = re.search(
                r'computed match rate is[^\d]*(\d+)|'
                r'match rate[^\d]*(\d+)|'
                r'Your.*?score.*?(\d+)%',
                page_text, re.IGNORECASE
            )
            if m:
                score = int(next(g for g in m.groups() if g is not None))
                logger.info(f"Score from match text: {score}")
            else:
                numbers = re.findall(r'\b(\d{1,3})\b', page_text[:2000])
                for n in numbers:
                    n_int = int(n)
                    if 0 <= n_int <= 100:
                        score = n_int
                        logger.info(f"Score fallback: {score}")
                        break

            if score is None:
                score = 0

            # Extract keywords from results table
            matched_keywords = []
            missing_keywords = []
            rows = re.findall(
                r'([A-Za-z][A-Za-z ]{1,35}?)\s{2,}(?:Hard|Soft|Other)\s+(\d+)%\s+\d+\s+\d+',
                page_text
            )
            for keyword, pct in rows:
                keyword = keyword.strip()
                if int(pct) > 0:
                    matched_keywords.append(keyword)
                else:
                    missing_keywords.append(keyword)

            logger.info(f"Done → score={score}, matched={len(matched_keywords)}, missing={len(missing_keywords)}")

            return {
                "score": score,
                "matched_keywords": matched_keywords,
                "missing_keywords": missing_keywords,
                "error": None
            }

        except Exception as e:
            logger.error(f"Scan error: {e}", exc_info=True)
            self._context = None  # reset so next call re-logs in fresh
            return {
                "score": 0, "matched_keywords": [], "missing_keywords": [],
                "error": str(e)
            }
        finally:
            await page.close()