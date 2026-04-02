"""
scanner.py — Playwright ATS Scanner with EXACT locators from SkillSyncer
All selectors verified live on app.skillsyncer.com
"""

import os
import json
import re
import asyncio
import logging
import shutil
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

# ── Confirmed URLs ───────────────────────────────────────────
LOGIN_URL     = "https://skillsyncer.com/login"
DASHBOARD_URL = "https://app.skillsyncer.com/dashboard"

# ════════════════════════════════════════════════════════════
#  EXACT LOCATORS — verified live from SkillSyncer
# ════════════════════════════════════════════════════════════

# LOGIN PAGE
LOCATOR_EMAIL          = 'input[autocomplete="off"][type="text"]'   # email textbox
LOCATOR_PASSWORD       = 'input[type="password"]'                   # password textbox
LOCATOR_SIGNIN_BTN     = 'button:has-text("Sign In")'               # Sign In button

# DASHBOARD — NEW SCAN MODAL trigger
LOCATOR_NEW_SCAN_BTN   = 'button:has-text("New Scan")'              # sidebar New Scan

# NEW SCAN MODAL FIELDS
LOCATOR_COMPANY_INPUT  = 'input[placeholder="Amazon"]'              # Company Name input
LOCATOR_JOBTITLE_INPUT = 'input[placeholder="Project Manager"]'     # Job Title input
LOCATOR_JD_EDITOR      = '.ProseMirror >> nth=0'                    # Job Description rich text
LOCATOR_RESUME_EDITOR  = '.ProseMirror >> nth=1'                    # Resume rich text
LOCATOR_SCAN_BTN       = 'button:has-text("Scan"):not(:has-text("New Scan")):not(:has-text("Create Scan"))'

# RESULTS PAGE — SCORE
# Verified: <span class="font-bold tracking-tighter leading-none items-center transition duration-100 text-4xl">77</span>
# Inside: <div class="percent flex flex-1 items-center justify-center">
# Inside: <div class="circle">
LOCATOR_SCORE          = 'span.font-bold.tracking-tighter.leading-none'
LOCATOR_SCORE_PARENT   = 'div.percent'
LOCATOR_SCORE_CIRCLE   = 'div.circle'

# RESULTS PAGE — KEYWORDS TABLE
# Verified: <table> with <tbody> containing <tr class="monitor"> rows
LOCATOR_KW_TABLE       = 'table'
LOCATOR_KW_ROWS        = 'table tbody tr'
LOCATOR_KW_NAME        = 'td:nth-child(1) button'                   # keyword name button
LOCATOR_KW_TYPE        = 'td:nth-child(2)'                          # Hard / Soft / Other
LOCATOR_KW_SCORE_CELL  = 'td:nth-child(3)'                          # percentage cell (next sibling after role=cell)

# RESULTS PAGE — MISSING KEYWORDS BADGES (from report cards)
# Verified: <span class="bg-green-200 text-green-800 ... cursor-pointer">keyword</span>
# These are the clickable keyword badges in the report section
LOCATOR_MISSING_BADGES = 'span.bg-green-200.text-green-800'

# RESULTS PAGE — SCORE SECTIONS
LOCATOR_HARD_SKILLS_PCT  = 'h3:has-text("Hard Skills") ~ * [class*="percent"], h3:has-text("Hard Skills") + *'
LOCATOR_SOFT_SKILLS_PCT  = 'h3:has-text("Soft Skills") ~ * [class*="percent"]'


class ATSScanner:

    async def scan(
        self,
        resume_text: str,
        jd_text: str,
        cookies: list = None,
        user_id: int = None,
    ) -> dict:
        """
        Full scan flow:
        1. Load session (cookies or master login)
        2. Open New Scan modal
        3. Fill Company, Job Title, JD, Resume
        4. Click Scan
        5. Extract score + all keywords
        """
        async with async_playwright() as p:
            browser = None
            args = ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            try:
                browser = await p.chromium.launch(headless=True, args=args)
            except Exception as e:
                logger.warning(f"Default Chromium launch failed: {e}")
                chrome_path = self._find_chrome_executable()
                if chrome_path:
                    logger.info(f"Trying explicit Chrome executable: {chrome_path}")
                    browser = await p.chromium.launch(
                        headless=True,
                        executable_path=chrome_path,
                        args=args,
                    )
                else:
                    raise

            try:
                # ── Build context with cookies ───────────────
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                )

                if cookies:
                    # User's own cookies — inject directly
                    await context.add_cookies(cookies)
                    logger.info(f"Injected cookies for user {user_id}")
                else:
                    # Try master session file
                    master_session = os.getenv("MASTER_SESSION_PATH", "master_session.json")
                    if os.path.exists(master_session):
                        await context.close()
                        context = await browser.new_context(
                            storage_state=master_session,
                            user_agent=(
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                            )
                        )
                        logger.info("Loaded master session")
                    else:
                        # Fresh master login
                        page = await context.new_page()
                        login_ok = await self._login(page)
                        if not login_ok:
                            await browser.close()
                            return {"error": "Master login failed"}
                        await context.storage_state(path="master_session.json")
                        logger.info("Master session saved")
                        await page.close()

                page = await context.new_page()

                # ── Verify session is valid ──────────────────
                await page.goto(DASHBOARD_URL, wait_until="domcontentloaded")
                await asyncio.sleep(2)

                if "login" in page.url:
                    logger.warning(f"Session expired for user {user_id}")
                    await browser.close()
                    return {"error": "session_expired"}

                logger.info(f"On dashboard: {page.url}")

                # ── Run the scan ─────────────────────────────
                result = await self._do_scan(page, resume_text, jd_text)
                await browser.close()
                return result

            except Exception as e:
                logger.error(f"Scanner error: {e}")
                try:
                    await browser.close()
                except:
                    pass
                return {"error": str(e)}

    # ════════════════════════════════════════════════════════
    #  BROWSER HELPERS
    # ════════════════════════════════════════════════════════

    def _find_chrome_executable(self) -> str | None:
        paths = [
            os.environ.get("CHROME_PATH"),
            os.environ.get("GOOGLE_CHROME_BIN"),
            os.environ.get("CHROME_BIN"),
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/google-chrome-beta",
            "/usr/bin/google-chrome-unstable",
            "/usr/bin/chrome",
        ]
        for path in paths:
            if path and os.path.exists(path):
                return path

        for name in ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome"]:
            found = shutil.which(name)
            if found:
                return found

        return None

    # ════════════════════════════════════════════════════════
    #  LOGIN (master account only)
    # ════════════════════════════════════════════════════════
    async def _login(self, page) -> bool:
        email = os.getenv("MASTER_EMAIL")
        password = os.getenv("MASTER_PASSWORD")

        if not email or not password:
            logger.error("MASTER_EMAIL / MASTER_PASSWORD not in .env")
            return False

        async def _find_input(selectors):
            for selector in selectors:
                try:
                    element = page.locator(selector).first
                    if await element.count() and await element.is_visible(timeout=2000):
                        return element
                except Exception:
                    continue
            return None

        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2)

            email_input = await _find_input([
                'input[type=email]',
                'input[name*=email]',
                'input[placeholder*="Email"]',
                'input[placeholder*="email"]',
                'input[autocomplete="off"][type="text"]',
                'input[id*=email]',
                'input[class*=email]',
                'input[role="textbox"]'
            ])
            if not email_input:
                try:
                    email_input = page.get_by_role("textbox", name=re.compile("email", re.I))
                except Exception:
                    email_input = page.locator('input[type=email], input[type=text]').first

            password_input = await _find_input([
                'input[type=password]',
                'input[name*=password]',
                'input[placeholder*="Password"]',
                'input[id*=password]',
                'input[class*=password]'
            ])
            if not password_input:
                try:
                    password_input = page.get_by_role("textbox", name=re.compile("password", re.I))
                except Exception:
                    password_input = page.locator('input[type=password]').first

            sign_in_button = await _find_input([
                'button:has-text("Sign In")',
                'button:has-text("Sign in")',
                'button:has-text("Log In")',
                'button:has-text("Log in")',
                'button[type=submit]',
                'button[name*=login]',
                'button[name*=signin]'
            ])
            if not sign_in_button:
                try:
                    sign_in_button = page.get_by_role("button", name=re.compile("sign.*in|log.*in|submit", re.I))
                except Exception:
                    sign_in_button = page.locator('button').filter(has_text=re.compile("sign in|log in|submit", re.I)).first

            if not email_input or not password_input or not sign_in_button:
                logger.error("Login form elements not found")
                return False

            await email_input.fill(email)
            await asyncio.sleep(0.4)
            await password_input.fill(password)
            await asyncio.sleep(0.4)
            await sign_in_button.click()

            try:
                await page.wait_for_url("**/dashboard**", timeout=60000)
            except Exception:
                await asyncio.sleep(5)
                await page.wait_for_load_state("networkidle", timeout=60000)

            if "login" in page.url:
                logger.error("Login failed — still on login page")
                return False

            logger.info("Master login successful!")
            return True

        except Exception as e:
            logger.error(f"Login error: {e}")
            return False

    # ════════════════════════════════════════════════════════
    #  FULL SCAN FLOW
    # ════════════════════════════════════════════════════════
    async def _do_scan(self, page, resume_text: str, jd_text: str) -> dict:
        try:
            # ── Step 1: Click "New Scan" button in sidebar ──
            # EXACT: button with text "New Scan" ref=e37
            try:
                await page.wait_for_selector('button:has-text("New Scan"), a:has-text("New Scan")', timeout=90000)
                await page.locator('button:has-text("New Scan"), a:has-text("New Scan")').first.click()
            except Exception:
                # fallback to broader selectors
                for sel in [
                    'button:has-text("New Scan")',
                    'a:has-text("New Scan")',
                    'text=New Scan',
                    'button[name="New Scan"]',
                    'button:has-text("Create Scan")',
                    'button:has-text("New scan")',
                ]:
                    try:
                        await page.wait_for_selector(sel, timeout=10000)
                        await page.locator(sel).first.click()
                        break
                    except Exception:
                        continue
            await asyncio.sleep(4)
            logger.info("New Scan modal opened")

            # ── Step 2: Fill Company Name ────────────────────
            # EXACT: input[placeholder="Amazon"] — Company Name
            await page.locator('input[placeholder="Amazon"]').fill("Company")
            await asyncio.sleep(0.3)

            # ── Step 3: Fill Job Title ───────────────────────
            # EXACT: input[placeholder="Project Manager"] — Job Title
            await page.locator('input[placeholder="Project Manager"]').fill("Position")
            await asyncio.sleep(0.3)

            # ── Step 4: Fill Job Description ─────────────────
            # EXACT: .ProseMirror nth=0 — rich text editor for JD
            jd_editor = page.locator('.ProseMirror').nth(0)
            await jd_editor.wait_for(state='visible', timeout=60000)
            await jd_editor.click()
            await asyncio.sleep(0.3)
            try:
                await jd_editor.fill(jd_text)
            except Exception:
                await jd_editor.evaluate("el => el.innerText = ''")
                await page.keyboard.type(jd_text, delay=5)
            await asyncio.sleep(0.5)

            # ── Step 5: Fill Resume ──────────────────────────
            # EXACT: .ProseMirror nth=1 — rich text editor for Resume
            resume_editor = page.locator('.ProseMirror').nth(1)
            await resume_editor.wait_for(state='visible', timeout=60000)
            await resume_editor.click()
            await asyncio.sleep(0.3)
            try:
                await resume_editor.fill(resume_text)
            except Exception:
                await resume_editor.evaluate("el => el.innerText = ''")
                await page.keyboard.type(resume_text, delay=5)
            await asyncio.sleep(0.5)

            # ── Step 6: Click Scan button ────────────────────
            # EXACT: button "Scan" exact=True (not New Scan / Create Scan)
            scan_btn = None
            for sel in [
                'button:has-text("Scan")',
                'button:has-text("Start Scan")',
                'button:has-text("Analyze")',
                'button:has-text("Submit")',
                'button[type=submit]'
            ]:
                try:
                    btn = page.locator(sel).first
                    if await btn.count() and await btn.is_visible(timeout=3000):
                        scan_btn = btn
                        break
                except Exception:
                    continue
            if not scan_btn:
                raise Exception("Could not find Scan button")
            await scan_btn.click()
            logger.info("Scan submitted — waiting for results...")

            # ── Step 7: Wait for results page ───────────────
            try:
                await page.wait_for_url("**/scans/**", timeout=120000)
            except Exception:
                logger.info("Scan results URL not detected; waiting for results content...")
                await page.wait_for_load_state("networkidle", timeout=120000)
                await asyncio.sleep(5)
                if "/scans/" not in page.url:
                    logger.warning(f"Unexpected URL after scan: {page.url}")
            logger.info(f"Results page: {page.url}")

            logger.info(f"Results page: {page.url}")

            # ── Step 8: Close upgrade popup if appears ───────
            try:
                close_btn = page.get_by_role("button", name="Close")
                if await close_btn.is_visible(timeout=3000):
                    await close_btn.click()
                    await asyncio.sleep(0.5)
            except:
                pass

            # ── Step 9: Extract everything ───────────────────
            return await self._extract_results(page)

        except Exception as e:
            logger.error(f"Scan flow error: {e}")
            # Save debug screenshot
            try:
                await page.screenshot(path="debug_scan.png", full_page=True)
            except:
                pass
            return {"error": str(e)}

    # ════════════════════════════════════════════════════════
    #  EXTRACT RESULTS — EXACT SELECTORS VERIFIED
    # ════════════════════════════════════════════════════════
    async def _extract_results(self, page) -> dict:
        return await page.evaluate("""
        () => {
            const result = {
                score: null,
                matched_keywords: [],
                missing_keywords: [],
                all_keywords: [],
                hard_skills_score: null,
                soft_skills_score: null,
            };

            // ══════════════════════════════════════════
            //  ATS SCORE
            //  EXACT: span.font-bold.tracking-tighter.leading-none
            //  Inside div.percent > div.circle
            //  Verified value: "77"
            // ══════════════════════════════════════════
            const scoreEl = document.querySelector(
                'span.font-bold.tracking-tighter.leading-none'
            );
            if (scoreEl) {
                result.score = scoreEl.textContent.trim();
            }

            // ══════════════════════════════════════════
            //  KEYWORDS TABLE
            //  EXACT: table tbody tr  (class="monitor" on each row)
            //  Columns: [keyword button] [type div] [score td] [resume count] [job count]
            // ══════════════════════════════════════════
            const rows = document.querySelectorAll('table tbody tr');
            rows.forEach(row => {
                const cells = row.querySelectorAll('td');
                if (cells.length < 3) return;

                // keyword name — inside button in first td
                const keywordBtn = cells[0].querySelector('button');
                const keyword = keywordBtn
                    ? keywordBtn.textContent.trim()
                    : cells[0].textContent.trim();

                // type — second td, has a div inside
                const type = cells[1].textContent.trim();  // "Hard" / "Soft" / "Other"

                // score % — the td after type (index 2 is a [role=cell] for %)
                // From live page: cells[2] is the score % column
                const scoreText = cells[2]?.textContent.trim() || '';
                const scoreNum = parseInt(scoreText.replace('%','')) || 0;

                // resume count & job count
                const resumeCount = cells[3]?.textContent.trim() || '0';
                const jobCount    = cells[4]?.textContent.trim() || '0';

                const entry = { keyword, type, score: scoreText, resumeCount, jobCount };
                result.all_keywords.push(entry);

                // Matched = score > 0% (found in resume)
                if (scoreNum > 0) {
                    result.matched_keywords.push(keyword);
                } else {
                    // Missing = score 0% (not in resume)
                    result.missing_keywords.push(keyword);
                }
            });

            // ══════════════════════════════════════════
            //  HARD/SOFT SKILLS SECTION SCORES
            //  EXACT: heading h3 contains "Hard Skills"
            //  Score is sibling with class containing percent number
            // ══════════════════════════════════════════
            document.querySelectorAll('h3').forEach(h3 => {
                const text = h3.textContent.trim();
                const sibling = h3.closest('div')?.querySelector(
                    '[class*="percent"], [class*="score-text"]'
                );
                const pctEl = h3.parentElement?.parentElement?.querySelector(
                    '*:last-child'
                );

                if (text === 'Hard Skills') {
                    // Find the % number next to "Hard Skills" heading
                    const parent = h3.closest('[class]');
                    if (parent) {
                        const allSpans = parent.querySelectorAll('*');
                        allSpans.forEach(el => {
                            if (el.textContent.match(/^\\d+%$/)) {
                                result.hard_skills_score = el.textContent.trim();
                            }
                        });
                    }
                }
                if (text === 'Soft Skills') {
                    const parent = h3.closest('[class]');
                    if (parent) {
                        const allSpans = parent.querySelectorAll('*');
                        allSpans.forEach(el => {
                            if (el.textContent.match(/^\\d+%$/)) {
                                result.soft_skills_score = el.textContent.trim();
                            }
                        });
                    }
                }
            });

            return result;
        }
        """)