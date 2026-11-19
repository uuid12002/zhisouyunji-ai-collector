"""Switch observed page controls and verify their state before submission."""

import asyncio
import time
from collector.domain.errors import CollectionError


class ThinkingControl:
    def __init__(self, page, config, actions, gate):
        self.page, self.rules = page, config.get('thinking', {})
        self.actions, self.gate = actions, gate

    async def unique(self, locator, timeout=0):
        deadline = time.monotonic() + timeout
        while True:
            visible = [e for e in await locator.all() if await e.is_visible()]
            if visible or time.monotonic() >= deadline:
                break
            deadline += await self.gate.wait_if_needed()
            await asyncio.sleep(0.1)
        if len(visible) != 1:
            raise CollectionError(
                'THINKING_MODE_UNAVAILABLE', '思考模式控件不唯一或不可见，未发送问题。'
            )
        return visible[0]

    async def observe(self, timeout=0):
        selector = self.rules.get('control')
        if not selector:
            raise CollectionError(
                'THINKING_MODE_UNAVAILABLE', '该平台尚未配置可核验的思考模式控件，未发送问题。'
            )
        control = await self.unique(self.page.locator(selector), timeout)
        attribute = self.rules.get('state_attribute')
        value = await control.get_attribute(attribute) if attribute else await control.inner_text()
        value = ' '.join((value or '').split())
        on, off = self.rules.get('on_values', []), self.rules.get('off_values', [])
        state = (
            True
            if value in on and value not in off
            else False
            if value in off and value not in on
            else None
        )
        return control, state, value

    async def confirm(self, requested):
        _, state, value = await self.observe()
        if state is not requested:
            raise CollectionError(
                'THINKING_MODE_UNVERIFIED', '思考模式状态与任务设置不一致，未发送问题。'
            )
        return {
            'thinking_mode': state,
            'thinking_mode_status': 'verified',
            'thinking_mode_label': value,
        }

    async def configure(self, requested):
        await self.gate.wait_if_needed()
        try:
            control, state, value = await self.observe(timeout=3 if requested is not None else 0)
        except CollectionError:
            if requested is not None:
                raise
            return {'thinking_mode': None, 'thinking_mode_status': 'platform_default_unverified'}
        if requested is None:
            return {
                'thinking_mode': state,
                'thinking_mode_status': 'observed_platform_default'
                if state is not None
                else 'platform_default_unverified',
                'thinking_mode_label': value,
            }
        if state is None and self.rules.get('kind') != 'menu':
            raise CollectionError(
                'THINKING_MODE_UNVERIFIED', f'无法识别当前思考模式“{value[:60]}”，未发送问题。'
            )
        if state is not requested:
            if self.rules.get('kind') == 'toggle':
                await self.actions.click(control)
            elif self.rules.get('kind') == 'menu':
                await self.actions.click(control)
                root_selector = self.rules.get('menu', '[role="menu"]')
                root = await self.unique(self.page.locator(root_selector), timeout=3)
                candidates = []
                for label in self.rules.get('on_values' if requested else 'off_values', []):
                    candidates.extend(
                        [
                            e
                            for e in await root.get_by_text(label, exact=True).all()
                            if await e.is_visible()
                        ]
                    )
                if len(candidates) != 1:
                    raise CollectionError(
                        'THINKING_MODE_UNAVAILABLE', '思考模式菜单选项不唯一或不可见，未发送问题。'
                    )
                target = candidates[0]
                disabled = await target.evaluate(
                    'e=>!!e.closest("[disabled],[aria-disabled=true]")'
                )
                if disabled:
                    raise CollectionError(
                        'THINKING_MODE_UNAVAILABLE', '所选思考模式暂不可用，未发送问题。'
                    )
                await self.actions.click(target)
            else:
                raise CollectionError(
                    'THINKING_MODE_UNAVAILABLE', '思考模式切换方式未配置，未发送问题。'
                )
        deadline = time.monotonic() + 3
        clear = 0
        while time.monotonic() < deadline:
            deadline += await self.gate.wait_if_needed()
            try:
                _, state, _ = await self.observe()
            except CollectionError:
                state = None
            clear = clear + 1 if state is requested else 0
            if clear >= 2:
                return await self.confirm(requested)
            await asyncio.sleep(0.1)
        raise CollectionError('THINKING_MODE_UNVERIFIED', '思考模式切换未生效，未发送问题。')
