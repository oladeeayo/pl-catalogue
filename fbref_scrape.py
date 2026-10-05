"""Fetch FBref PL stat pages via Pydoll + Cloudflare turnstile (HEADFUL).

Headless is blocked by Cloudflare (403); a real Chrome window solves the
turnstile. Standalone/manual step - NOT part of run_weekly.

Pages (files that exist are skipped unless FORCE=1), saved into sources/:
    sources/pl_shooting.html, sources/fbref_passing.html, sources/fbref_possession.html,
    sources/fbref_defense.html, sources/fbref_misc.html
"""
import asyncio
import os
from pathlib import Path

from pydoll.browser.chromium.chrome import Chrome
from pydoll.browser.options import ChromiumOptions

BASE = "https://fbref.com/en/comps/9/2026-2027/{stat}/2026-2027-Premier-League-Stats"
SRC = Path(__file__).parent / "sources"
PAGES = {
    "shooting": "pl_shooting.html",
    "passing": "fbref_passing.html",
    "possession": "fbref_possession.html",
    "defense": "fbref_defense.html",
    "misc": "fbref_misc.html",
}
FORCE = os.environ.get("FORCE", "") == "1"


async def fetch_all():
    SRC.mkdir(exist_ok=True)
    opts = ChromiumOptions()
    opts.binary_location = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    opts.headless = False
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-blink-features=AutomationControlled")

    async with Chrome(options=opts) as browser:
        tab = await browser.start()
        for stat, out_name in PAGES.items():
            out = SRC / out_name
            if out.exists() and not FORCE:
                print(f"skip {out_name} (exists)", flush=True)
                continue
            url = BASE.format(stat=stat)
            html_text = ""
            try:
                async with tab.expect_cloudflare_turnstile(time_to_wait_captcha=15):
                    await tab.go_to(url)
            except Exception as e:  # noqa: BLE001 - challenge may not appear
                print(f"turnstile note {stat}: {e}", flush=True)
            for attempt in range(24):
                try:
                    resp = await tab.request.get(url)
                    html_text = resp.text
                    status = resp.status_code
                except Exception as e:  # noqa: BLE001 - transient fetch error
                    status, html_text = f"err({type(e).__name__})", ""
                ok = (status == 200 and "Just a moment" not in html_text
                      and "<table" in html_text)
                if attempt % 4 == 0:
                    print(f"{stat} attempt {attempt}: status={status} "
                          f"len={len(html_text)} ok={ok}", flush=True)
                if ok:
                    break
                if attempt and attempt % 6 == 0:
                    try:
                        async with tab.expect_cloudflare_turnstile(time_to_wait_captcha=15):
                            await tab.go_to(url)
                    except Exception:  # noqa: BLE001
                        pass
                await asyncio.sleep(2)
            if not html_text or "<table" not in html_text:
                print(f"FAILED {stat}", flush=True)
                continue
            out.write_text(html_text, encoding="utf-8")
            print(f"saved {out_name} ({len(html_text)} bytes)", flush=True)


if __name__ == "__main__":
    asyncio.run(fetch_all())
