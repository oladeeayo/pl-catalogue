import asyncio

from pydoll.browser.chromium.chrome import Chrome
from pydoll.browser.options import ChromiumOptions

URL = "https://fbref.com/en/comps/9/2026-2027/shooting/2026-2027-Premier-League-Stats"
OUT = "pl_shooting.html"


async def main():
    opts = ChromiumOptions()
    opts.binary_location = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    opts.headless = True
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-blink-features=AutomationControlled")

    async with Chrome(options=opts) as browser:
        tab = await browser.start()

        async with tab.expect_cloudflare_turnstile(time_to_wait_captcha=10):
            await tab.go_to(URL)

        html = ""
        for attempt in range(20):
            resp = await tab.request.get(URL)
            html = resp.text
            print(f"attempt {attempt}: status={resp.status_code} len={len(html)}")
            if resp.status_code == 200 and "Just a moment" not in html and "<table" in html:
                break
            await asyncio.sleep(2)

        with open(OUT, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"saved {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
