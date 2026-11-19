"""Real browser pixels on a synthetic wide, tall table (not platform acceptance)."""
import asyncio
import tempfile
from pathlib import Path
from playwright.async_api import async_playwright
from capture import capture_segments


async def main():
    with tempfile.TemporaryDirectory() as temp:
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(channel='msedge',headless=True)
            page=await browser.new_page(viewport={'width':1000,'height':700})
            await page.set_content('''<article style="width:600px"><p>Start</p>
              <div id="wide" style="overflow-x:auto;width:600px"><div style="width:1600px;height:1100px;background:linear-gradient(90deg,red,blue)">First column <span style="float:right">LAST COLUMN</span></div></div><p>End</p></article>''')
            files,complete=await capture_segments(page,page.locator('article'),Path(temp),'answer')
            wide=[f for f in files if 'start_x' in f['coverage']]
            assert wide and complete
            assert max(f['coverage']['end_x'] for f in wide)>=1600
            assert max(f['coverage']['end_y'] for f in wide)>=1100
            assert await page.locator('#wide').evaluate('e=>e.scrollLeft')==0
            assert all((Path(temp)/f['name']).read_bytes().startswith(b'\x89PNG') for f in files)
            await browser.close()
    print('PASS: horizontal and vertical table coverage; final column; scroll restoration. Synthetic fixture.')


asyncio.run(main())
