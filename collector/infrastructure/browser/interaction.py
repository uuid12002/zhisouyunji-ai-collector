"""Replaceable browser-event and Windows desktop interaction implementations."""

import asyncio
import uuid
import time

from collector.domain.errors import CollectionError


class BrowserActions:
    mode = 'browser'
    backend = 'playwright'

    def __init__(self, page, config, human_gate):
        self.page, self.config, self.human_gate = page, config, human_gate

    def close(self):
        pass

    async def click(self, locator, *, preflight=None):
        await self.human_gate.wait_if_needed()
        if preflight:
            await preflight()
        await locator.click(timeout=5000)

    async def fill(self, locator, question):
        method = self.config.get('input_method', 'fill')
        if method == 'keyboard':
            await self.click(locator)
            await locator.press('ControlOrMeta+A')
            await locator.press('Backspace')
            for index, line in enumerate(question.split('\n')):
                await self.human_gate.wait_if_needed()
                if index:
                    await locator.press('Shift+Enter')
                await locator.press_sequentially(line)
        elif method == 'fill':
            await self.human_gate.wait_if_needed()
            await locator.fill(question)
        else:
            raise CollectionError('ADAPTER_NOT_CONFIGURED', '未知输入方式，未发送。')


class DesktopActions(BrowserActions):
    mode = 'desktop_rpa'
    backend = 'windows_sendinput'

    def __init__(self, page, config, human_gate, desktop=None):
        super().__init__(page, config, human_gate)
        if desktop is None:
            from .native_windows import WindowsDesktop

            desktop = WindowsDesktop()
        self.desktop = desktop
        self.bound = False

    def close(self):
        self.desktop.close()

    async def bind(self):
        if self.bound:
            self.desktop.ensure_target()
            return
        await self.page.bring_to_front()
        title = await self.page.title()
        token = 'collector-rpa-' + uuid.uuid4().hex
        try:
            await self.page.evaluate('(token)=>document.title=token', token)
            await asyncio.sleep(0.2)
            self.desktop.bind(token)
            await asyncio.sleep(0.2)
            if not self.desktop.is_foreground():
                if self.human_gate.report:
                    await self.human_gate.report('waiting_window')
                deadline = time.monotonic() + 30
                while not self.desktop.is_foreground() and time.monotonic() < deadline:
                    await asyncio.sleep(0.1)
                if self.desktop.is_foreground() and self.human_gate.report:
                    await self.human_gate.report('resuming')
            self.desktop.ensure_target()
            self.bound = True
        finally:
            if not self.page.is_closed():
                await self.page.evaluate('(title)=>document.title=title', title)

    async def click(self, locator, *, preflight=None):
        await self.human_gate.wait_if_needed()
        await self.bind()
        if (
            await locator.count() != 1
            or not await locator.is_visible()
            or not await locator.is_enabled()
        ):
            raise CollectionError('RPA_TARGET_CHANGED', '点击目标不可见、不唯一或不可用，未点击。')
        await locator.scroll_into_view_if_needed()
        # Reobserve after scroll/maximize; never reuse coordinates across actions.
        geometry = await locator.evaluate("""e=>{
            const r=e.getBoundingClientRect(), w=innerWidth, h=innerHeight;
            const l=Math.max(0,r.left), t=Math.max(0,r.top), rr=Math.min(w,r.right), bb=Math.min(h,r.bottom);
            const x=(l+rr)/2,y=(t+bb)/2, at=document.elementFromPoint(x,y);
            return {x,y,w,h,hit:rr>l && bb>t && (e===at || e.contains(at)),
                scale:visualViewport ? visualViewport.scale : 1};
        }""")
        if not geometry['hit'] or abs(geometry['scale'] - 1) > 0.01:
            raise CollectionError(
                'RPA_TARGET_CHANGED', '目标被页面遮挡或页面使用手势缩放，未点击。'
            )
        left, top, width, height = self.desktop.renderer_rect()
        scale_x, scale_y = width / geometry['w'], height / geometry['h']
        if not 0.5 <= scale_x <= 4 or abs(scale_x - scale_y) > 0.08:
            raise CollectionError(
                'RPA_COORDINATES',
                f'内容区与页面尺寸不匹配，停止点击。屏幕内容区 {width}×{height}，网页视口 {geometry["w"]}×{geometry["h"]}，比例 {scale_x:.3f}/{scale_y:.3f}。',
            )
        if preflight:
            await preflight()
        self.desktop.click(left + geometry['x'] * scale_x, top + geometry['y'] * scale_y)
        await asyncio.sleep(0.05)

    async def ensure_editor(self, locator):
        await self.human_gate.wait_if_needed()
        self.desktop.ensure_target()
        if not await locator.evaluate(
            '(e)=>document.hasFocus() && (e===document.activeElement || e.contains(document.activeElement))'
        ):
            raise CollectionError('RPA_EDITOR_FOCUS_LOST', '输入框已失去焦点，已停止键盘输入。')

    async def fill(self, locator, question):
        question = question.replace('\r\n', '\n').replace('\r', '\n')
        if any(ord(char) < 32 and char != '\n' for char in question):
            raise CollectionError(
                'RPA_UNSUPPORTED_TEXT', '桌面 RPA 不支持制表符或其他控制字符，请改用浏览器操作。'
            )
        try:
            question.encode('utf-16-le')
        except UnicodeEncodeError as exc:
            raise CollectionError(
                'RPA_UNSUPPORTED_TEXT', '问题包含无法输入的 Unicode 字符，未输入。'
            ) from exc
        await self.click(locator)
        await self.ensure_editor(locator)
        self.desktop.chord(0x11, 0x41)  # Ctrl+A, never a clipboard paste.
        await asyncio.sleep(0.04)
        await self.ensure_editor(locator)
        self.desktop.chord(0x08)  # Backspace.
        await asyncio.sleep(0.04)
        for index, line in enumerate(question.split('\n')):
            await self.ensure_editor(locator)
            if index:
                self.desktop.chord(0x10, 0x0D)  # Shift+Enter, not Send.
                await asyncio.sleep(0.04)
            # Check DOM focus and manual challenge between short chunks as well.
            for start in range(0, len(line), 16):
                await self.ensure_editor(locator)
                self.desktop.type_text(line[start : start + 16])
                await asyncio.sleep(0.01)
        await self.ensure_editor(locator)
