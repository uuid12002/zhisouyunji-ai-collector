"""Real local browser pixels: blocked screenshots must not lose text/share exports."""

import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
from playwright.async_api import async_playwright
from collector.infrastructure.browser.adapter import Adapter


class PartialExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_overlay_preserves_text_and_attempts_share_without_fake_image(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(channel='msedge', headless=True)
                try:
                    page = await browser.new_page()
                    await page.set_content("""<p id="question">测试问题</p>
                        <article id="answer">保留全部回答正文。第二行内容。</article>
                        <div style="position:fixed;inset:0;z-index:999;background:white">遮挡</div>""")
                    adapter = Adapter(page, {'question': '#question', 'answer': '#answer'})
                    copy = AsyncMock(
                        return_value={'share_url': 'https://example.invalid/share/local-fixture'}
                    )
                    with patch('collector.infrastructure.browser.adapter.extract_share_link', copy):
                        result = await adapter.collect('测试问题', folder, True, 'yuanbao')
                    copy.assert_awaited_once()
                    self.assertEqual(result['answer'], '保留全部回答正文。第二行内容。')
                    self.assertEqual(
                        (folder / 'answer.txt').read_text(encoding='utf-8'), result['answer']
                    )
                    self.assertTrue(result['share_url'])
                    self.assertFalse(result['exports_complete'])
                    self.assertFalse(result['long_image_complete'])
                    self.assertFalse((folder / 'answer-long.png').exists())
                    self.assertTrue(any('EVIDENCE_INCOMPLETE' in w for w in result['limitations']))
                finally:
                    await browser.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
