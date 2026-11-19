"""Expose a paused task's existing browser; never interact with login/challenges."""

from playwright.async_api import Error as BrowserError


async def request_attention(page):
    # Restoring a minimized window helps users see a challenge on another platform.
    # Platforms and Windows may still require the user to click its taskbar icon.
    cdp = None
    try:
        cdp = await page.context.new_cdp_session(page)
        window = await cdp.send('Browser.getWindowForTarget')
        if window['bounds'].get('windowState') == 'minimized':
            await cdp.send(
                'Browser.setWindowBounds',
                {
                    'windowId': window['windowId'],
                    'bounds': {'windowState': 'normal'},
                },
            )
    except BrowserError:
        if page.is_closed():
            raise
    finally:
        if cdp:
            await cdp.detach()
    await page.bring_to_front()
