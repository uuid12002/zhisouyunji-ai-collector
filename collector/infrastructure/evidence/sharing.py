"""Obtain a link through the site's share UI, never from the address bar."""

import asyncio
import time
import uuid
import re
from urllib.parse import urlparse
from collector.domain.errors import CollectionError


def valid_share_url(value, conversation_url, hosts):
    try:
        url = urlparse(value.strip())
        return (
            url.scheme == 'https'
            and url.hostname in hosts
            and not url.username
            and not url.password
            and url.path not in ('', '/')
            and value.strip().rstrip('/') != conversation_url.rstrip('/')
        )
    except (ValueError, AttributeError):
        return False


async def extract_share_link(page, config, human_gate=None, actions=None):
    result = {
        'share_url': None,
        'share_status': 'unavailable',
        'share_method': 'platform_share_button',
    }
    button_selector = config.get('share_button')
    if not button_selector:
        return result | {'share_error': '尚未配置此平台的分享按钮。'}
    hosts = config.get('share_hosts') or [urlparse(page.url).hostname]
    conversation = page.url

    async def clipboard():
        try:
            return await page.evaluate('navigator.clipboard.readText()')
        except Exception:
            return None

    async def click(locator):
        if actions is not None:
            await actions.click(locator)
        else:
            await locator.click(timeout=5000)

    marker = None
    previous = None
    try:
        await page.context.grant_permissions(
            ['clipboard-read', 'clipboard-write'],
            origin=f'{urlparse(page.url).scheme}://{urlparse(page.url).netloc}',
        )
        button = page.locator(button_selector)
        if config.get('share_target') == 'last' and await button.count():
            button = button.last
        if await button.count() != 1:
            return result | {'share_error': '分享按钮不唯一或未出现，需要校准页面适配。'}
        await button.scroll_into_view_if_needed()
        await click(button)
        copied = False
        marker = 'collector-copy-' + uuid.uuid4().hex
        created = False
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if human_gate is not None:
                deadline += await human_gate.wait_if_needed()
            if not created and config.get('share_create'):
                create = page.locator(config['share_create'])
                if await create.count() == 1 and await create.is_visible():
                    await click(create)
                    created = True
            copies = page.locator(config['share_copy']) if config.get('share_copy') else None
            visible_copies = (
                [c for c in await copies.all() if await c.is_visible()]
                if copies is not None
                else []
            )
            copy = visible_copies[0] if len(visible_copies) == 1 else None
            if not copied and copy is not None:
                # Reset immediately before the explicit copy action. Even a previously
                # copied identical share URL must be attributable to this click.
                previous = await clipboard()
                await page.evaluate('(value)=>navigator.clipboard.writeText(value)', marker)
                await click(copy)
                copied = True
            if copied:
                value = await clipboard()
                candidates = re.findall(r'https://[^\s<>"\\]+', value or '')
                valid = {
                    v.rstrip('。，；）)')
                    for v in candidates
                    if valid_share_url(v.rstrip('。，；）)'), conversation, hosts)
                }
                if len(valid) == 1:
                    return result | {
                        'share_url': valid.pop(),
                        'share_status': 'captured',
                        'share_method': 'share_copy_button',
                    }
            await asyncio.sleep(0.25)
        return result | {
            'share_error': '未能通过分享弹窗的复制链接按钮取得链接；未使用地址栏地址。'
        }
    except CollectionError:
        raise
    except Exception:
        return result | {'share_error': '分享按钮操作失败，需检查平台登录或分享权限。'}
    finally:
        if marker and previous is not None:
            try:
                if await clipboard() == marker:
                    await page.evaluate('(value)=>navigator.clipboard.writeText(value)', previous)
            except Exception:
                pass
