"""Dismiss identified marketing dialogs, never downloads or verification prompts."""

import asyncio
import time
import uuid
from collector.domain.errors import CollectionError
from playwright.async_api import TimeoutError as BrowserTimeout

ROOTS = '[role="dialog"], [aria-modal="true"], dialog, [class*="modal" i], [class*="popup" i]'
CLOSE = (
    'button[aria-label*="关闭"], [role="button"][aria-label*="关闭"], '
    '[aria-label*="close" i], [title="关闭"], [data-testid*="close" i], '
    '[class*="close" i], button'
)


class PromotionGuard:
    def __init__(self, page, config, actions, human_gate, timeout=120, interval=0.5):
        self.page, self.config, self.actions, self.gate = page, config, actions, human_gate
        self.timeout, self.interval = timeout, interval
        self.paused_seconds = 0

    async def dialogs(self):
        rules = self.config.get('promotion_dialogs', [])
        if not rules:
            return []
        found = []
        for root in await self.page.locator(ROOTS).all():
            if not await root.is_visible():
                continue
            # A quoted advertisement in an answer is not a modal.
            token = uuid.uuid4().hex
            try:
                snapshot = await root.evaluate(
                    """(e, token) => {
              if(e.closest('article,[data-testid="receive_message"],.ds-message'))return false;
              let overlay=false;
              for(let n=e;n && n!==document.body;n=n.parentElement){
                const s=getComputedStyle(n);
                if(n.getAttribute('role')==='dialog'||n.getAttribute('aria-modal')==='true'
                  ||n.tagName==='DIALOG'||(['fixed','absolute'].includes(s.position)&&Number(s.zIndex)>=10)){overlay=true;break;}
              }const r=e.getBoundingClientRect();if(overlay)e.setAttribute('data-collector-promotion',token);return overlay?{text:e.innerText,
                box:{x:r.x,y:r.y,width:r.width,height:r.height}}:false;
            }""",
                    token,
                    timeout=300,
                )
            except BrowserTimeout:
                continue
            if not snapshot:
                continue
            text = snapshot['text']
            if len(text) > 5000:
                continue
            if any(all(marker in text for marker in rule['texts']) for rule in rules):
                box = snapshot['box']
                if box and box['width'] >= 200 and box['height'] >= 100:
                    stable = self.page.locator(f'[data-collector-promotion="{token}"]')
                    found.append((box['width'] * box['height'], stable, box))
        return sorted(found, key=lambda entry: entry[0])

    async def close_button(self, root, bounds):
        choices = []
        for node in await root.locator(CLOSE).all():
            if not await node.is_visible():
                continue
            token = uuid.uuid4().hex
            try:
                info = await node.evaluate(
                    """(e,token)=>{e.setAttribute('data-collector-promotion-action',token);const r=e.getBoundingClientRect();return {
                  tag:e.tagName,label:e.getAttribute('aria-label')||e.title||'',
                  testid:e.getAttribute('data-testid')||'',cls:typeof e.className==='string'?e.className:'',text:e.innerText||'',
                  box:{x:r.x,y:r.y,width:r.width,height:r.height}}}""",
                    token,
                    timeout=300,
                )
            except BrowserTimeout:
                continue
            if info['tag'] not in ('BUTTON', 'DIV', 'SPAN', 'A'):
                continue
            if '下载' in info['text'] + info['label'] or 'download' in info['label'].lower():
                continue
            known = (
                '关闭' in info['label']
                or 'close' in info['label'].lower()
                or 'close' in info['testid'].lower()
                or 'close' in info['cls'].lower()
                or info['text'].strip() in ('×', '✕', 'X', 'x', '关闭')
            )
            if not known:
                continue
            box = info['box']
            if not box or not (8 <= box['width'] <= 90 and 8 <= box['height'] <= 90):
                continue
            if (
                box['x'] < bounds['x'] + bounds['width'] * 0.65
                or box['y'] > bounds['y'] + bounds['height'] * 0.25
            ):
                continue
            choices.append(self.page.locator(f'[data-collector-promotion-action="{token}"]'))
        # Do not choose among two matching close controls, even if they look alike.
        return choices[0] if len(choices) == 1 else None

    async def dismiss(self):
        await self.gate.wait_if_needed()
        for text in self.config.get('dismiss_promotion_texts', []):
            visible = [
                n
                for n in await self.page.get_by_text(text, exact=True).all()
                if await n.is_visible()
            ]
            if len(visible) == 1:
                await self.actions.click(visible[0])
        dialogs = await self.dialogs()
        if not dialogs:
            return 0
        for _, root, bounds in dialogs:
            button = await self.close_button(root, bounds)
            if button is not None:
                await self.actions.click(button)
                try:
                    await root.wait_for(state='hidden', timeout=2000)
                    return 0
                except Exception:
                    # Never repeat a close click whose outcome is unknown.
                    break
        started = time.monotonic()
        human_pause = self.gate.paused_seconds
        if self.gate.report:
            await self.gate.report('waiting_overlay')
        try:
            while time.monotonic() - started < self.timeout:
                if self.page.is_closed():
                    raise CollectionError('BROWSER_CLOSED', '等待关闭推广弹窗时浏览器已关闭。')
                await self.gate.wait_if_needed()
                if not await self.dialogs():
                    if self.gate.report:
                        await self.gate.report('resuming')
                    return time.monotonic() - started
                await asyncio.sleep(self.interval)
            raise CollectionError(
                'PROMOTION_BLOCKED',
                '请手动关闭豆包客户端下载推广弹窗；程序不会点击下载或重复提问。',
            )
        finally:
            self.paused_seconds += max(
                0, time.monotonic() - started - (self.gate.paused_seconds - human_pause)
            )
