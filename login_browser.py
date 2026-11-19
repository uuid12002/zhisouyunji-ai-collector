"""Dedicated local validation browser; no credentials leave its profile."""
import asyncio
import sys
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    platform=sys.argv[1] if len(sys.argv)>1 else 'doubao'
    port=19333 if platform=='doubao' else 19334
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(Path('data/profiles',platform).resolve()), channel='msedge', headless=False,
            args=[f'--remote-debugging-port={port}', '--remote-debugging-address=127.0.0.1'],
            viewport={'width':1280,'height':900})
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto('https://www.doubao.com/chat/' if platform=='doubao' else 'https://chat.deepseek.com/',wait_until='domcontentloaded')
        while context.pages:
            await asyncio.sleep(1)

asyncio.run(main())
