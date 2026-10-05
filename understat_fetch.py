import asyncio
from pathlib import Path

from pydoll.browser.chromium.chrome import Chrome
from pydoll.browser.options import ChromiumOptions

PAGE = "https://understat.com/league/EPL/2026"
API = "https://understat.com/getLeagueData/EPL/2026"
OUT = Path(__file__).parent / "sources" / "understat_league.json"


async def main():
    opts = ChromiumOptions()
    opts.binary_location = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    opts.headless = True
    opts.add_argument("--disable-notifications")

    async with Chrome(options=opts) as browser:
        tab = await browser.start()
        await tab.go_to(PAGE)
        await asyncio.sleep(3)
        resp = await tab.request.get(API)
        print("status:", resp.status_code, "len:", len(resp.text))
        OUT.parent.mkdir(exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(resp.text)


if __name__ == "__main__":
    asyncio.run(main())
