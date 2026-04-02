"""
ATS Scanner — uses SkillSyncer via Playwright browser automation.
Exposes ATSScanner class with async scan() method to match bot.py usage.
"""

import asyncio
import logging
import os
import re
import shutil
from collections import Counter

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

            email_input = None
            password_input = None
            sign_in_button = None

            # Robust email field selection
            for selector in [
                "input[type=email]",
                "input[name*=email]",
                "input[placeholder*='Email']",
                "input[placeholder*='email']",
                "input[id*=email]",
                "input[class*=email]"
            ]:
                try:
                    el = page.locator(selector).first
                    if await el.count() and await el.is_visible(timeout=2000):
                        email_input = el
                        break
                except Exception:
                    continue

            if email_input is None:
                email_input = page.get_by_role("textbox", name=re.compile("email", re.I))

            # Robust password field selection
            for selector in [
                "input[type=password]",
                "input[name*=password]",
                "input[placeholder*='Password']",
                "input[id*=password]",
                "input[class*=password]"
            ]:
                try:
                    el = page.locator(selector).first
                    if await el.count() and await el.is_visible(timeout=2000):
                        password_input = el
                        break
                except Exception:
                    continue

            if password_input is None:
                password_input = page.get_by_role("textbox", name=re.compile("password", re.I))

            # Robust sign-in button selection
            for selector in [
                "button:has-text('Sign In')",
                "button:has-text('Sign in')",
                "button[type=submit]",
                "button:has-text('Login')",
                "button:has-text('Log In')"
            ]:
                try:
                    btn = page.locator(selector).first
                    if await btn.count() and await btn.is_visible(timeout=2000):
                        sign_in_button = btn
                        break
                except Exception:
                    continue

            if email_input is None or password_input is None or sign_in_button is None:
                raise Exception("Login form not found")

            await email_input.fill(account["email"])
            await password_input.fill(account["password"])
            await sign_in_button.click()
            await page.wait_for_load_state("networkidle", timeout=30000)
            await page.wait_for_url("**/dashboard", timeout=30000)
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
            match = re.search(r'Scans\s*Left[:\s]+(\d+)', text, re.IGNORECASE)
            if not match:
                match = re.search(r'(\d+)\s+scans?\s+left', text, re.IGNORECASE)
            if not match:
                match = re.search(r'(\d+)\s+remaining', text, re.IGNORECASE)
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

    async def _set_field_text(self, locator, text: str):
        tag = await locator.evaluate("el => el.tagName.toLowerCase()")
        if tag in ("textarea", "input"):
            await locator.fill(text)
            return

        await locator.evaluate(
            "(el, value) => {"
            "  el.focus();"
            "  if ('value' in el) el.value = value;"
            "  if ('innerText' in el) el.innerText = value;"
            "  el.textContent = value;"
            "  el.dispatchEvent(new InputEvent('input', { bubbles: true }));"
            "  el.dispatchEvent(new Event('change', { bubbles: true }));"
            "}",
            text
        )
        await asyncio.sleep(0.1)

    async def _collect_editor_fields(self, page):
        fields = []
        editors = page.locator('.ProseMirror, div[contenteditable="true"], textarea')
        count = await editors.count()
        for idx in range(count):
            locator = editors.nth(idx)
            hint = await locator.evaluate(
                "el => {"
                "  let node = el;"
                "  while (node) {"
                "    if (node.previousElementSibling && node.previousElementSibling.textContent) {"
                "      const text = node.previousElementSibling.textContent.trim();"
                "      if (text) return text;"
                "    }"
                "    node = node.parentElement;"
                "  }"
                "  return '';"
                "}"
            )
            fields.append((locator, hint.lower() if hint else ""))
        return fields

    def _match_field_order(self, fields):
        if len(fields) < 2:
            return None

        hints = [hint for _, hint in fields]
        if any("resume" in hint for hint in hints) and any("job" in hint or "description" in hint for hint in hints):
            resume_index = next(i for i, h in enumerate(hints) if "resume" in h)
            jd_index = next(i for i, h in enumerate(hints) if "job" in h or "description" in h)
            return resume_index, jd_index
        return 0, 1

    def _detect_subscription_page(self, page_url: str, text: str) -> bool:
        if "subscription" in page_url or "billing" in page_url or "payment" in page_url:
            return True
        lower = text.lower()
        return "subscription" in lower or "upgrade" in lower or "billing" in lower or "paid plan" in lower

    def _wait_for_scan_results(self, page):
        return page.wait_for_function(
            "() => {"
            "  const text = document.body.innerText.toLowerCase();"
            "  return /match rate|score|result|matched keywords|missing keywords|your score/.test(text);"
            "}",
            timeout=90000
        )

    def _extract_keywords(self, text: str) -> list[str]:
        cleaned = re.sub(r'[\r\n]+', ' ', text)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        stopwords = {
            'and', 'or', 'the', 'for', 'with', 'from', 'that', 'this', 'these', 'those',
            'will', 'have', 'has', 'your', 'you', 'are', 'is', 'in', 'on', 'to', 'of',
            'a', 'an', 'as', 'be', 'by', 'at', 'we', 'our', 'us', 'may', 'also', 'can',
            'experience', 'skills', 'skill', 'work', 'team', 'requirements', 'required',
            'responsibilities', 'including', 'demonstrated', 'ability', 'strong', 'years',
            'experience', 'using', 'business', 'technical', 'knowledge', 'candidate'
        }

        # Use meaningful JD lines first
        lines = []
        for raw_line in text.splitlines():
            line = raw_line.strip(' -•*\t')
            if not line:
                continue
            if len(line) > 100:
                parts = re.split(r'[,:;] +', line)
                for part in parts:
                    part = part.strip()
                    if 15 <= len(part) <= 90:
                        lines.append(part)
            else:
                lines.append(line)

        candidates = []
        for line in lines:
            lower = line.lower()
            if len(line.split()) >= 3 and any(term in lower for term in [
                'experience', 'experience with', 'familiar', 'knowledge of',
                'responsibilities', 'required', 'strong', 'prefer', 'must have',
                'ability to', 'work with', 'manage', 'design', 'build', 'develop'
            ]):
                candidates.append(re.sub(r'[\.:;]+$', '', line).strip())

        words = re.findall(r"[A-Za-z0-9+#]+", text)
        freq = Counter(
            w.lower() for w in words
            if len(w) > 2 and w.lower() not in stopwords and not w.isdigit()
        )
        for word, _ in freq.most_common(40):
            if word not in stopwords and word not in {c.lower() for c in candidates}:
                candidates.append(word)
            if len(candidates) >= 30:
                break

        cleaned_keywords = []
        seen = set()
        for item in candidates:
            item_norm = re.sub(r'\s+', ' ', item).strip()
            if len(item_norm) < 3:
                continue
            key = item_norm.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned_keywords.append(item_norm)
            if len(cleaned_keywords) >= 30:
                break

        return cleaned_keywords

    def _phrase_in_text(self, text: str, phrase: str) -> bool:
        phrase_norm = re.sub(r'\s+', ' ', phrase.lower()).strip()
        if not phrase_norm:
            return False
        if phrase_norm in text:
            return True
        words = [w for w in phrase_norm.split() if len(w) > 2]
        if len(words) >= 2 and all(w in text for w in words):
            return True
        return False

    def _local_scan(self, resume_text: str, jd_text: str) -> dict:
        resume_norm = re.sub(r'\s+', ' ', resume_text.lower())
        keywords = self._extract_keywords(jd_text)
        matched_keywords = []
        missing_keywords = []

        for keyword in keywords:
            if self._phrase_in_text(resume_norm, keyword):
                matched_keywords.append(keyword)
            else:
                missing_keywords.append(keyword)

        score = 0
        if keywords:
            score = round(len(matched_keywords) / len(keywords) * 100)

        logger.info(f"Local fallback score={score}, matched={len(matched_keywords)}, missing={len(missing_keywords)}")

        return {
            "score": score,
            "matched_keywords": matched_keywords,
            "missing_keywords": missing_keywords,
            "error": None
        }

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
                "button:has-text('New scan')",
                "a:has-text('New Scan')",
                "a:has-text('New scan')",
                "text=New Scan",
                "text=New scan",
                "button[name='New Scan']",
                "button[type=button] >> text=New Scan"
            ]:
                try:
                    el = page.locator(selector).first
                    if await el.count() and await el.is_visible(timeout=2000):
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

            page_text = await page.evaluate("() => document.body.innerText")
            if self._detect_subscription_page(current_url, page_text):
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
                return await self._do_scan(resume_text, jd_text, _retry=_retry+1)

            # Detect what kind of input the modal uses
            html = await page.content()
            has_contenteditable = 'contenteditable="true"' in html or 'contenteditable=' in html
            has_prosemirror = 'ProseMirror' in html
            has_textarea = '<textarea' in html
            logger.info(f"contenteditable={has_contenteditable} ProseMirror={has_prosemirror} textarea={has_textarea}")

            if has_prosemirror or has_contenteditable or has_textarea:
                await asyncio.sleep(1)
                fields = await self._collect_editor_fields(page)
                if not fields:
                    raise Exception("No fillable fields found in scan form")

                pair = self._match_field_order(fields)
                if pair is None or len(fields) < 2:
                    raise Exception("Unable to determine scan input fields")

                resume_index, jd_index = pair
                if resume_index >= len(fields) or jd_index >= len(fields):
                    raise Exception("Scan form fields are fewer than expected")

                resume_field, _ = fields[resume_index]
                jd_field, _ = fields[jd_index]

                await self._set_field_text(resume_field, resume_text)
                logger.info("Resume filled.")
                await self._set_field_text(jd_field, jd_text)
                logger.info("JD filled.")
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

            try:
                await page.wait_for_url("**/scans/**", timeout=90000)
            except Exception:
                logger.info("Scan results URL not detected; waiting for result contents instead.")
                await self._wait_for_scan_results(page)

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