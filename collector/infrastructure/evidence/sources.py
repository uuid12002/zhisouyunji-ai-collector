"""Source extraction paths observed on the logged-in websites on 2026-10-23."""

import asyncio
import re
from urllib.parse import urlparse


async def extract_sources(page, answer, platform, folder, actions=None):
    async def click(locator):
        if actions is None:
            await locator.click()
        else:
            await actions.click(locator)

    refs = []
    files = []
    warnings = []

    async def evidence(loc):
        name = f'source-{len(files) + 1:03d}.png'
        await loc.screenshot(path=str(folder / name), timeout=15000)
        files.append({'name': name, 'kind': 'sources_screenshot', 'mime': 'image/png'})
        return name

    def add(title, url, relation, labels=None, names=None):
        if url and urlparse(url).scheme not in ('https', 'http'):
            url = None
        refs.append(
            {
                'id': f'source-{len(refs) + 1}',
                'title': title,
                'displayed_url': url,
                'resolved_url': None,
                'url_status': 'captured' if url else 'unavailable',
                'relation': relation,
                'citation_labels': labels or [],
                'evidence_files': names or [],
            }
        )

    inline = await answer.locator('a[href]').evaluate_all("""es=>es.map(e=>({title:e.innerText,
      href:e.getAttribute('data-copy-href')||e.href, label:e.getAttribute('aria-label'),
      cited:!!e.closest('[data-citation-group]')}))""")
    by_url = {}
    for item in inline:
        key = item['href']
        if key not in by_url:
            by_url[key] = item | {'labels': []}
        if item['cited'] and item['label'] and item['label'] not in by_url[key]['labels']:
            by_url[key]['labels'].append(item['label'])
    completed = True
    if platform in ('yuanbao', 'qianwen'):
        completed = False
        warnings.append(
            '当前仅提取回答正文直接提供的链接；该平台引用面板与编号引用尚待真实账号验收。'
        )
    try:
        if platform == 'doubao':
            area = page.get_by_test_id('receive_message')
            banner = area.get_by_text(re.compile(r'^搜索 \d+ 个关键词，参考 \d+ 篇资料$'))
            if await banner.count() == 1:
                expected = int(re.search(r'参考 (\d+)', await banner.inner_text()).group(1))
                cards = area.locator('a[data-thinking-box-tool-call="true"]')
                if not await cards.count() or not await cards.first.is_visible():
                    await click(banner)
                    await asyncio.sleep(0.5)
                count = await cards.count()
                for index in range(count):
                    card = cards.nth(index)
                    name = await evidence(card)
                    add(
                        await card.inner_text(),
                        await card.get_attribute('href'),
                        'search_result',
                        names=[name],
                    )
                completed = count == expected
            pills = answer.locator('span.container-sWvQla')
            for index in range(await pills.count()):
                pill = pills.nth(index)
                await pill.hover()
                tip = page.locator('[role="tooltip"].semi-popover-wrapper-show')
                await tip.wait_for(state='visible', timeout=5000)
                title = tip.locator('.title-ehsWfR')
                if await title.count() != 1:
                    completed = False
                    continue
                name = await evidence(tip)
                # The current tooltip exposes a title but no URL. Never infer its URL from a title match.
                add(await title.inner_text(), None, 'cited', [await pill.inner_text()], [name])
                await page.mouse.move(0, 0)
                await asyncio.sleep(0.2)
        elif platform == 'deepseek':
            area = page.locator(
                '[data-virtual-list-item-key]:has(.ds-assistant-message-main-content)'
            )
            trigger = area.locator('.f93f59e4')
            if await trigger.count() == 1:
                expected = int(re.search(r'(\d+)\s*个网页', await trigger.inner_text()).group(1))
                cards = page.locator('a:has(.search-view-card__title)')
                if not await cards.count() or not await cards.first.is_visible():
                    await click(trigger)
                await cards.first.wait_for(state='visible', timeout=5000)
                count = await cards.count()
                for index in range(count):
                    card = cards.nth(index)
                    url = await card.get_attribute('href')
                    item = by_url.pop(url, None)
                    labels = item['labels'] if item else []
                    name = await evidence(card)
                    add(
                        await card.locator('.search-view-card__title').inner_text(),
                        url,
                        'cited' if labels else 'search_result',
                        labels,
                        [name],
                    )
                completed = count == expected
    except Exception:
        completed = False
        warnings.append('来源区域提取未完成，已保留可读取的条目。')
    for item in by_url.values():
        add(item['title'], item['href'], 'cited' if item['labels'] else 'unknown', item['labels'])
    return refs, files, completed, warnings
