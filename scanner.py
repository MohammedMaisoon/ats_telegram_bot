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

# Account pool — rotates when one runs out of scans
_DEFAULT_PASSWORD = os.environ.get("MASTER_PASSWORD", "ski!!@123")
ACCOUNTS = [
    {"email": os.environ.get("MASTER_EMAIL", "maisoonmohammed23@gmail.com"), "password": _DEFAULT_PASSWORD},
    {"email": "mohammedmaisoon24@gmail.com", "password": _DEFAULT_PASSWORD},
    {"email": "mohammedmaisoon22@gmail.com", "password": _DEFAULT_PASSWORD},
    {"email": "hmoideenbasha@gmail.com",     "password": _DEFAULT_PASSWORD},
    {"email": "mdmaisoon23@gmail.com",       "password": _DEFAULT_PASSWORD},
]
MASTER_EMAIL    = ACCOUNTS[0]["email"]
MASTER_PASSWORD = ACCOUNTS[0]["password"]

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
        self._playwright    = None
        self._browser       = None
        self._context       = None
        self._lock          = asyncio.Lock()
        self._account_index = 0   # which account we are currently using

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

    async def _login(self, account=None):
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

        if account is None:
            account = ACCOUNTS[self._account_index]

        await self._start_browser()
        self._context = await self._browser.new_context()
        page = await self._context.new_page()
        try:
            logger.info(f"Logging in as {account['email']}...")
            await page.goto(
                "https://skillsyncer.com/login",
                wait_until="domcontentloaded",
                timeout=30000
            )
            await page.get_by_role("textbox", name="you@mail.com").fill(account["email"])
            await page.get_by_role("textbox", name="password").fill(account["password"])
            await page.get_by_role("button", name="Sign In").click()
            await page.wait_for_url("**/dashboard", timeout=20000)
            logger.info(f"Login successful: {account['email']}")
            return True
        except Exception as e:
            logger.error(f"Login failed for {account['email']}: {e}")
            self._context = None
            return False
        finally:
            await page.close()

    async def _login_next_account(self):
        """Try each account in the pool until one works and has scans."""
        for i in range(len(ACCOUNTS)):
            idx = (self._account_index + i) % len(ACCOUNTS)
            account = ACCOUNTS[idx]
            logger.info(f"Trying account {idx+1}/{len(ACCOUNTS)}: {account['email']}")

            # Fully reset browser + context before each attempt
            if self._context:
                try:
                    await self._context.close()
                except Exception:
                    pass
                self._context = None
            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    pass
                self._browser = None

            ok = await self._login(account)
            if not ok:
                continue
            scans = await self._scans_remaining()
            if scans > 0:
                self._account_index = idx
                logger.info(f"Using account: {account['email']} ({scans} scans left)")
                return True
            else:
                logger.warning(f"Account {account['email']} has 0 scans — trying next")
        logger.error("All accounts exhausted — no scans available")
        return False

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

    async def _do_scan(self, resume_text: str, jd_text: str, _retry: int = 0) -> dict:
        if _retry >= 5:
            return {"score": 0, "matched_keywords": [], "missing_keywords": [], "error": "All SkillSyncer accounts are out of scans. Resets every Sunday!"}
        # Ensure we have a logged-in session with scans available
        if self._context is None:
            logger.info("No session — finding account with scans...")
            ok = await self._login_next_account()
            if not ok:
                return {
                    "score": 0, "matched_keywords": [], "missing_keywords": [],
                    "error": "All SkillSyncer accounts are out of scans. Resets every Sunday!"
                }
        else:
            logger.info("Reusing existing session.")

        # Check scans remaining — if 0, rotate to next account
        remaining = await self._scans_remaining()
        if remaining == 0:
            logger.warning("Current account has 0 scans — rotating to next account...")
            if self._context:
                try:
                    await self._context.close()
                except Exception:
                    pass
                self._context = None
            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    pass
                self._browser = None
            self._account_index = (self._account_index + 1) % len(ACCOUNTS)
            ok = await self._login_next_account()
            if not ok:
                return {
                    "score": 0, "matched_keywords": [], "missing_keywords": [],
                    "error": "All SkillSyncer accounts are out of scans. Resets every Sunday!"
                }

        page = await self._context.new_page()
        try:
            await page.goto(
                "https://app.skillsyncer.com/dashboard",
                wait_until="domcontentloaded",
                timeout=20000
            )

            # Open New Scan modal
            logger.info("Clicking New Scan button...")
            await page.wait_for_load_state("networkidle", timeout=10000)

            # Try multiple selectors for the New Scan button
            clicked = False
            for selector in [
                "button:has-text('New Scan')",
                "a:has-text('New Scan')",
                "text=New Scan",
                "button[name='New Scan']",
            ]:
                try:
                    el = page.locator(selector).first
                    if await el.is_visible(timeout=2000):
                        await el.click()
                        clicked = True
                        logger.info(f"Clicked: {selector}")
                        break
                except Exception:
                    continue
            if not clicked:
                raise Exception("Could not find New Scan button")

            await asyncio.sleep(4)
            current_url = page.url
            logger.info(f"URL after click: {current_url}")

            # If redirected to subscription page, this account has no scans — rotate
            if "subscription" in current_url:
                logger.warning("Redirected to subscription — account out of scans, rotating...")
                try:
                    await page.close()
                except Exception:
                    pass
                if self._context:
                    try:
                        await self._context.close()
                    except Exception:
                        pass
                    self._context = None
                if self._browser:
                    try:
                        await self._browser.close()
                    except Exception:
                        pass
                    self._browser = None
                self._account_index = (self._account_index + 1) % len(ACCOUNTS)
                ok = await self._login_next_account()
                if not ok:
                    return {
                        "score": 0, "matched_keywords": [], "missing_keywords": [],
                        "error": "All SkillSyncer accounts are out of scans. Resets every Sunday!"
                    }
                # Restart scan with new account
                return await self._do_scan(resume_text, jd_text, _retry=_retry+1)

            # Detect what kind of input the modal uses
            html = await page.content()
            has_contenteditable = 'contenteditable="true"' in html
            has_prosemirror = 'ProseMirror' in html
            has_textarea = '<textarea' in html
            logger.info(f"contenteditable={has_contenteditable} ProseMirror={has_prosemirror} textarea={has_textarea}")

            if has_prosemirror or has_contenteditable:
                # Wait for the editor boxes
                await page.wait_for_selector('.ProseMirror, div[contenteditable="true"]', timeout=15000)
                await asyncio.sleep(1)
                editors = page.locator('.ProseMirror, div[contenteditable="true"]')
                count = await editors.count()
                logger.info(f"Found {count} editor boxes")

                jd_box = editors.nth(0)
                await jd_box.scroll_into_view_if_needed()
                await jd_box.click()
                await asyncio.sleep(0.3)
                await page.keyboard.type(jd_text, delay=5)
                logger.info("JD filled.")

                resume_box = editors.nth(1)
                await resume_box.scroll_into_view_if_needed()
                await resume_box.click()
                await asyncio.sleep(0.3)
                await page.keyboard.type(resume_text, delay=5)
                logger.info("Resume filled.")

            elif has_textarea:
                await page.wait_for_selector('textarea', timeout=15000)
                textareas = page.locator('textarea')
                await textareas.nth(0).fill(jd_text)
                await textareas.nth(1).fill(resume_text)
                logger.info("Filled via textarea.")

            else:
                raise Exception(f"No input found on page. URL={current_url}")

            # Click Scan submit button
            for sel in ["button:has-text('Scan')", "button[name='Scan']", "button[type='submit']"]:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        logger.info(f"Scan submitted via: {sel}")
                        break
                except Exception:
                    continue
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