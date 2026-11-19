"""DOM adapter. Selectors and Playwright operations stay in infrastructure."""

import asyncio
import time
from datetime import datetime, timezone
from collector.domain.errors import CollectionError
from .human_gate import HumanGate
from .interaction import BrowserActions, DesktopActions
from .promotions import PromotionGuard
from .thinking import ThinkingControl
from collector.infrastructure.evidence.capture import capture_segments, stitch_answer
from collector.infrastructure.evidence.sources import extract_sources
from collector.infrastructure.evidence.sharing import extract_share_link


def normalize(text):
    return ' '.join(text.split())


class Adapter:
    def __init__(self, page, config, report=None):
        self.page, self.config = (page, config)
        self.report = report
        self.login_timeout = 600
        self.login_poll_interval = 1
        self.human_gate = HumanGate(page, config, report)
        actions = (
            DesktopActions if config.get('interaction_mode') == 'desktop_rpa' else BrowserActions
        )
        self.actions = actions(page, config, self.human_gate)
        self.promotions = PromotionGuard(page, config, self.actions, self.human_gate)
        self.thinking = ThinkingControl(page, config, self.actions, self.human_gate)

    async def configure_thinking(self, requested):
        return await self.thinking.configure(requested)

    async def confirm_thinking(self, requested):
        if requested is not None:
            return await self.thinking.confirm(requested)

    def loc(self, key):
        if not self.config.get(key):
            raise CollectionError(
                'ADAPTER_NOT_CONFIGURED', f'平台定位规则 {key} 尚未通过真实页面配置。'
            )
        return self.page.locator(self.config[key])

    async def one(self, key):
        loc = self.loc(key)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            deadline += await self.human_gate.wait_if_needed()
            deadline += await self.dismiss_known_overlays()
            visible = [item for item in await loc.all() if await item.is_visible()]
            if len(visible) > 1:
                break
            if len(visible) == 1:
                return visible[0]
            await asyncio.sleep(0.2)
        raise CollectionError('UI_CHANGED', f'{key} 元素不唯一或不可见，已停止，未猜测点击。')

    async def dismiss_known_overlays(self):
        return await self.promotions.dismiss()

    async def login_required(self, platform):
        if self.config.get('login_required'):
            markers = self.loc('login_required')
            for marker in await markers.all():
                if await marker.is_visible():
                    return True
        return '/sign_in' in self.page.url or (
            platform == 'doubao' and await self.page.get_by_test_id('to_login_button').is_visible()
        )

    async def check_login(self, platform):
        await self.human_gate.wait_if_needed()
        if not await self.login_required(platform):
            return
        if self.report:
            await self.report('waiting_login')
        from .attention import request_attention

        await request_attention(self.page)
        deadline = time.monotonic() + self.login_timeout
        clear = 0
        while time.monotonic() < deadline:
            deadline += await self.human_gate.wait_if_needed()
            clear = 0 if await self.login_required(platform) else clear + 1
            if clear >= 2:
                if self.report:
                    await self.report('resuming')
                return
            await asyncio.sleep(self.login_poll_interval)
        raise CollectionError(
            'LOGIN_REQUIRED',
            '等待手动登录超时，已保留专用浏览器。请登录并保留窗口，核对原任务后再操作。',
        )

    async def configure(self, requested):
        if requested is None:
            if self.config.get('search_toggle') and self.config.get('search_state_attribute'):
                toggle = await self.one('search_toggle')
                state = await toggle.get_attribute(self.config['search_state_attribute'])
                if state in (
                    self.config.get('search_on_value'),
                    self.config.get('search_off_value'),
                ):
                    return {
                        'web_search': state == self.config['search_on_value'],
                        'web_search_status': 'observed_platform_default',
                    }
            return {'web_search': None, 'web_search_status': 'platform_default_unverified'}
        selector = self.config.get('search_toggle')
        attr = self.config.get('search_state_attribute')
        on = self.config.get('search_on_value')
        off = self.config.get('search_off_value')
        if not selector or not attr or on is None or (off is None):
            raise CollectionError(
                'UNSUPPORTED_OPTION', '尚无法核验联网开关。请选择“平台默认”或完成联网选项校准。'
            )
        toggle = await self.one('search_toggle')
        state = await toggle.get_attribute(attr)
        if state not in (on, off):
            raise CollectionError('UI_CHANGED', '联网状态属性无法识别。')
        if (state == on) != requested:
            await self.actions.click(toggle)
            await asyncio.sleep(0.3)
        if await toggle.get_attribute(attr) != (on if requested else off):
            raise CollectionError('UNSUPPORTED_OPTION', '联网选项未生效。')
        return {'web_search': requested, 'web_search_status': 'verified'}

    async def current_question(self, question):
        loc = await self.one('question')
        if normalize(await loc.inner_text()) != normalize(question):
            raise CollectionError(
                'CONVERSATION_MISMATCH', '页面问题与任务不一致，不会采集其他会话。'
            )
        return loc

    async def wait_finished(self, question, timeout=300):
        last = None
        stable = 0
        stable_since = time.monotonic()
        deadline = time.monotonic() + timeout
        paused_before = self.human_gate.paused_seconds
        promotion_pause = self.promotions.paused_seconds
        while (
            time.monotonic()
            < deadline
            + self.human_gate.paused_seconds
            - paused_before
            + self.promotions.paused_seconds
            - promotion_pause
        ):
            await self.current_question(question)
            visible_answers = [
                item for item in await self.loc('answer').all() if await item.is_visible()
            ]
            if len(visible_answers) == 1:
                answers = visible_answers[0]
                text = await answers.inner_text()
                if not text or text != last: stable_since = time.monotonic()
                stable = stable + 1 if text and text == last else 0
                last = text
                busy = self.loc('busy') if self.config.get('busy') else None
                active = (
                    any([await item.is_visible() for item in await busy.all()])
                    if busy is not None
                    else False
                )
                done = self.loc('finished')
                if (
                    await done.count() == 1
                    and await done.is_visible()
                    and (not active)
                    and text and (time.monotonic() - stable_since >= 6)
                ):
                    return True
            await asyncio.sleep(0.5)
        return False

    async def collect(self, question, folder, answer_complete, platform):
        q = await self.current_question(question)
        answer = await self.one('answer')
        text = await answer.inner_text()
        if not text.strip():
            raise CollectionError('EMPTY_ANSWER', '网页没有可采集的回答正文。', False)
        html = await answer.evaluate('(e)=>e.outerHTML')
        (folder / 'answer.html').write_text(html, encoding='utf-8')
        (folder / 'answer.txt').write_text(text, encoding='utf-8')
        question_files, question_covered = await capture_segments(self.page, q, folder, 'question')
        answer_files, answer_covered = await capture_segments(self.page, answer, folder, 'answer')
        capture_warnings = []
        if not question_files or not answer_files:
            missing = '、'.join(
                name
                for name, files in [('问题', question_files), ('回答', answer_files)]
                if not files
            )
            capture_warnings.append(
                f'EVIDENCE_INCOMPLETE：无法截取可见的{missing}区域；全文已保留，继续导出分享链接。请检查页面内弹窗及 geometry.json 诊断。'
            )
        coverage = await answer.evaluate(
            "e=>{\n          const all=[e,...e.querySelectorAll('*')];\n          return !all.some(n=>n.clientHeight>0 && n.scrollHeight>n.clientHeight+3 && /auto|scroll|hidden/.test(getComputedStyle(n).overflowY));\n        }"
        )
        references, source_files, sources_complete, warnings = await extract_sources(
            self.page, answer, platform, folder, self.actions
        )
        warnings.extend(capture_warnings)
        if text != await answer.inner_text():
            raise CollectionError('ANSWER_CHANGED', '截图期间回答发生变化，请恢复原会话重新采集。')
        long_image, long_warning = stitch_answer(folder, answer_files)
        if not coverage:
            long_image = None
            long_warning = '回答中存在折叠或内部滚动区域，长图完整性未通过核验。'
        if long_warning:
            warnings.append(long_warning)
        conversation_url = self.page.url
        await self.human_gate.wait_if_needed()
        share = await extract_share_link(self.page, self.config, self.human_gate, self.actions)
        if share.get('share_error'):
            warnings.append(share['share_error'])
        return {
            'mock': False,
            'interaction_mode': self.actions.mode,
            'interaction_backend': self.actions.backend,
            'answer': text,
            'references': references,
            **share,
            'exports_complete': bool(long_image and share.get('share_url') and question_files),
            'long_image_complete': bool(long_image),
            'long_image_name': long_image['name'] if long_image else None,
            'answer_complete': answer_complete,
            'screenshot_coverage': 'visible_segments_geometric_coverage'
            if coverage and question_covered and answer_covered
            else 'partial',
            'references_complete': sources_complete,
            'real_evidence_complete': False,
            'limitations': warnings + ['截图覆盖尚需人工核验，未宣称完整证据。'],
            'conversation_url': conversation_url,
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'extraction_method': 'dom',
            'files': question_files
            + answer_files
            + [
                {'name': 'answer.html', 'kind': 'answer_html', 'mime': 'text/html'},
                {'name': 'answer.txt', 'kind': 'answer_text', 'mime': 'text/plain'},
            ]
            + ([long_image] if long_image else [])
            + source_files,
        }

    @property
    def url(self):
        return self.page.url

    async def navigate(self, url):
        await self.page.goto(url, wait_until='domcontentloaded')
        await self.page.wait_for_timeout(2000)

    def validate_rules(self):
        for key in ('new_chat', 'input', 'send', 'question', 'answer', 'finished'):
            self.loc(key)

    async def new_chat(self):
        # A landing page may already be empty and have no "new chat" button.
        # Require a usable empty composer, not merely a temporarily empty DOM.
        if await self.wait_empty_chat(timeout=1.5):
            return
        await self.actions.click(await self.one('new_chat'))
        if not await self.wait_empty_chat(timeout=15):
            raise CollectionError(
                'NEW_CHAT_FAILED', '新会话未就绪：历史消息未清空或输入框不可用，未发送问题。'
            )

    async def wait_empty_chat(self, timeout):
        deadline = time.monotonic() + timeout
        stable_since = None
        while time.monotonic() < deadline:
            deadline += await self.human_gate.wait_if_needed()
            fields = [item for item in await self.loc('input').all() if await item.is_visible()]
            ready = len(fields) == 1 and await fields[0].is_editable()
            if ready:
                value = await fields[0].evaluate('(e)=>e.value === undefined ? e.innerText : e.value')
                ready = not (value or '').strip() and not await self.has_messages()
            if ready:
                if stable_since is None:
                    stable_since = time.monotonic()
                if time.monotonic() - stable_since >= .8:
                    return True
            else:
                stable_since = None
            await asyncio.sleep(.1)
        return False

    async def has_messages(self):
        for key in ('question', 'answer'):
            for item in await self.loc(key).all():
                if await item.is_visible() and (await item.inner_text()).strip():
                    return True
        return False

    async def fill_question(self, question):
        field = await self.one('input')
        await self.actions.fill(field, question)
        value = await field.evaluate('(e)=>e.value === undefined ? e.innerText : e.value')
        if normalize(value) != normalize(question):
            raise CollectionError('INPUT_MISMATCH', '输入内容与问题不一致，已停止，未发送。')
        await self.one('send')

    async def send(self):
        async def verify_mode():
            await self.confirm_thinking(self.config.get('requested_thinking_mode'))

        await self.actions.click(await self.one('send'), preflight=verify_mode)

    async def confirm_question(self, question):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            deadline += await self.human_gate.wait_if_needed()
            if await self.loc('question').count():
                await self.current_question(question)
                return
            await asyncio.sleep(0.25)
        raise CollectionError(
            'SUBMISSION_UNCERTAIN', '发送后未出现可核对的消息；请检查原会话，不会重复发送。'
        )
