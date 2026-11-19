"""Regression fixtures for the four observed share interfaces. No live requests."""

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

from collector.infrastructure.evidence.sharing import extract_share_link
from collector.presentation.report import render_report


async def main():
    configs = json.loads(Path('adapters.json').read_text(encoding='utf-8'))
    fixtures = {
        'doubao': (
            '<div data-testid="receive_message"><button data-testid="message_action_share"></button></div>',
            '<button data-testid="thread_share_copy_btn" hidden>复制链接</button><button data-testid="thread_share_copy_btn" id="copy">复制链接</button>',
            'https://www.doubao.com/thread/example',
        ),
        'deepseek': (
            '<div role="button" class="ds-button--xs"><svg><path d="M7.95889 1.52285"></path></svg></div>',
            '<button id="create">创建分享链接</button>',
            'https://chat.deepseek.com/share/example',
        ),
        'qianwen': (
            '<div data-answer-feedback-toolbar><div><svg><path d="M512 119.168"></path></svg></div></div>',
            '<button id="copy">复制链接</button>',
            'https://qianwen.my.cn/share/chat/example?lang=zh-CN',
        ),
        'yuanbao': (
            '<div aria-label="分享" class="Toolbar_shareIcon__test"></div>',
            '<div><div id="copy" role="button" aria-label="复制链接" data-share-type="link">图标</div><div class="agent-chat__share-bar__item__name">复制链接</div></div>',
            'https://yb.tencent.com/s/example',
        ),
    }
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            document = ''

            async def serve(route):
                await route.fulfill(body=document, content_type='text/html; charset=utf-8')

            await page.route('**/*', serve)
            for platform, (button, popup, url) in fixtures.items():
                document = (
                    '<style>[hidden]{display:none!important}svg{width:20px;height:20px}[role=button],button,[aria-label="分享"]{display:inline-block;min-width:30px;min-height:30px}</style>'
                    + button
                    + button
                )
                await page.goto(configs[platform]['url'])
                buttons = page.locator(configs[platform]['share_button'])
                assert await buttons.count() == 2, platform
                await buttons.first.evaluate("e=>e.onclick=()=>{throw Error('wrong older answer')}")
                await buttons.last.evaluate(
                    """(e,popup)=>e.onclick=()=>{
                    const dialog=document.createElement('section');dialog.innerHTML=popup;
                    document.body.append(dialog);window.shareClicked=true;
                    const create=document.querySelector('#create');
                    if(create)create.onclick=()=>{create.remove();const b=document.createElement('button');b.id='copy';b.textContent='创建并复制';dialog.append(b);window.wireCopy()};
                    window.wireCopy();
                }""",
                    popup,
                )
                await page.evaluate(
                    """url=>{window.wireCopy=()=>{const copy=document.querySelector('#copy');if(copy)copy.onclick=()=>{window.copyClicked=true;navigator.clipboard.writeText('点击查看回答\\n'+url)}}}""",
                    url,
                )
                result = await extract_share_link(page, configs[platform])
                assert result['share_url'] == url, (platform, result)
                assert await page.evaluate('window.shareClicked && window.copyClicked'), platform

            # Copy was clicked but did not write anything: old clipboard is rejected
            # and restored, rather than leaving the freshness marker for the user.
            document = '<button id="share">分享</button><button id="copy">复制链接</button><input value="https://chat.deepseek.com/share/decoy">'
            await page.goto('https://chat.deepseek.com/a/original')
            stale = 'https://chat.deepseek.com/share/stale'
            await page.evaluate('(v)=>navigator.clipboard.writeText(v)', stale)
            config = {
                'share_button': '#share',
                'share_copy': '#copy',
                'share_hosts': ['chat.deepseek.com'],
            }
            result = await extract_share_link(page, config)
            assert result['share_url'] is None, result
            assert await page.evaluate('navigator.clipboard.readText()') == stale
            await page.locator('#copy').evaluate(
                'e=>e.onclick=()=>navigator.clipboard.writeText(location.href)'
            )
            result = await extract_share_link(page, config)
            assert result['share_url'] is None, result
            report = render_report('test', {'answer': 'answer', 'conversation_url': page.url})
            assert page.url not in report
        finally:
            await browser.close()
    print(
        'PASS: four observed share selectors, latest answer, hidden duplicate, creation step, icon-only handler, fresh copy and address-bar rejection. Synthetic fixtures only.'
    )


if __name__ == '__main__':
    asyncio.run(main())
