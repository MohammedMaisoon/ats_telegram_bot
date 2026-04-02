"""
scanner.py — Playwright ATS Scanner with EXACT locators from SkillSyncer
All selectors verified live on app.skillsyncer.com
"""

import os
import json
import asyncio
import logging
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
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )

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
    #  LOGIN (master account only)
    # ════════════════════════════════════════════════════════
    async def _login(self, page) -> bool:
        email    = os.getenv("MASTER_EMAIL")
        password = os.getenv("MASTER_PASSWORD")

        if not email or not password:
            logger.error("MASTER_EMAIL / MASTER_PASSWORD not in .env")
            return False

        try:
            await page.goto(LOGIN_URL, wait_until="networkidle")
            await asyncio.sleep(1)

            # EXACT: input[autocomplete="off"][type="text"] → email
            await page.locator('input[autocomplete="off"][type="text"]').fill(email)
            await asyncio.sleep(0.4)

            # EXACT: input[type="password"] → password
            await page.locator('input[type="password"]').fill(password)
            await asyncio.sleep(0.4)

            # EXACT: button "Sign In"
            await page.get_by_role("button", name="Sign In").click()
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)

            if "login" in page.url:
                logger.error("Login failed — check credentials")
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
            await page.get_by_role("button", name="New Scan").click()
            await asyncio.sleep(2)
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
            await page.locator('.ProseMirror').nth(0).click()
            await asyncio.sleep(0.3)
            await page.locator('.ProseMirror').nth(0).fill(jd_text)
            await asyncio.sleep(0.5)

            # ── Step 5: Fill Resume ──────────────────────────
            # EXACT: .ProseMirror nth=1 — rich text editor for Resume
            await page.locator('.ProseMirror').nth(1).click()
            await asyncio.sleep(0.3)
            await page.locator('.ProseMirror').nth(1).fill(resume_text)
            await asyncio.sleep(0.5)

            # ── Step 6: Click Scan button ────────────────────
            # EXACT: button "Scan" exact=True (not New Scan / Create Scan)
            await page.get_by_role("button", name="Scan", exact=True).click()
            logger.info("Scan submitted — waiting for results...")

            # ── Step 7: Wait for results page ───────────────
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(5)

            # Confirm we're on scan results URL
            if "/scans/" not in page.url:
                logger.warning(f"Unexpected URL after scan: {page.url}")
                await asyncio.sleep(5)  # wait more

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
                            if (el.textContent.match(/^\d+%$/)) {
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
                            if (el.textContent.match(/^\d+%$/)) {
                                result.soft_skills_score = el.textContent.trim();
                            }
                        });
                    }
                }
            });

            return result;
        }
        """)