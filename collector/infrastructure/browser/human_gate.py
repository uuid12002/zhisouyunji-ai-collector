"""Recognize visible challenges and yield the existing browser to its user."""

import asyncio
import time

from collector.domain.errors import CollectionError


CHALLENGES = (
    '#captcha_container',
    '#captcha-verify-container',
    '[class*="captcha_verify_container"]',
    '.geetest_panel',
    '.geetest_box',
    '.tcaptcha-transform',
    '#tcaptcha_transform_dy',
    'iframe[src*="captcha"]',
    'iframe[src*="challenges.cloudflare.com"]',
    '[role="dialog"]:has-text("安全验证")',
    '[role="dialog"]:has-text("请完成人机验证")',
)


class HumanGate:
    def __init__(self, page, config, report=None, timeout=600, poll_interval=1):
        self.page, self.config, self.report = page, config, report
        self.timeout, self.poll_interval = timeout, poll_interval
        self.paused_seconds = 0
        self.last_detection = None

    async def detected(self):
        self.last_detection = None
        selectors = (*CHALLENGES, *self.config.get('human_verification', []))
        for selector in selectors:
            for element in await self.page.locator(selector).all():
                if await element.is_visible():
                    visible_challenge = await element.evaluate("""e=>{
                      for(let n=e;n;n=n.parentElement){
                        const s=getComputedStyle(n);
                        if(s.visibility!=='visible'||Number(s.opacity)<=0.01||
                           n.getAttribute('aria-hidden')==='true')return false;
                      }
                      const r=e.getBoundingClientRect();
                      const left=Math.max(0,r.left),right=Math.min(innerWidth,r.right);
                      const top=Math.max(0,r.top),bottom=Math.min(innerHeight,r.bottom);
                      if(right-left<=10||bottom-top<=10)return false;
                      // Background verification SDK frames are often left mounted.
                      // Only pause for a panel the user can actually see and reach.
                      for(const fx of [.2,.5,.8])for(const fy of [.2,.5,.8]){
                        const n=document.elementFromPoint(left+(right-left)*fx,top+(bottom-top)*fy);
                        if(n&&(n===e||e.contains(n)))return true;
                      }
                      return false;
                    }""")
                    if visible_challenge:
                        self.last_detection = {
                            'selector': selector,
                            'box': await element.bounding_box(),
                        }
                        return True
        return False

    async def wait_if_needed(self):
        if self.page.is_closed():
            raise CollectionError('BROWSER_CLOSED', '浏览器已关闭；请核对原会话，不会自动重发。')
        if not await self.detected():
            return 0
        started = time.monotonic()
        if self.report:
            await self.report('waiting_human', {'human_verification': self.last_detection})
        from .attention import request_attention

        await request_attention(self.page)
        try:
            # No clicking, typing, refreshing or CAPTCHA solving while challenged.
            clear_observations = 0
            while time.monotonic() - started < self.timeout:
                if self.page.is_closed():
                    raise CollectionError(
                        'HUMAN_VERIFICATION', '人工验证期间关闭了浏览器；请检查原会话。'
                    )
                clear_observations = 0 if await self.detected() else clear_observations + 1
                if clear_observations >= 2:
                    if self.report:
                        await self.report('resuming')
                    return time.monotonic() - started
                await asyncio.sleep(self.poll_interval)
            raise CollectionError(
                'HUMAN_VERIFICATION',
                '人工验证等待超过 10 分钟，已停止此平台后续任务；不会重复发送。',
            )
        finally:
            self.paused_seconds += time.monotonic() - started
