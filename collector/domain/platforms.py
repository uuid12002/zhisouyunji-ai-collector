"""Shared platform identifiers; keep browser dependencies out of the server."""

from typing import Literal

Platform = Literal['doubao', 'deepseek', 'yuanbao', 'qianwen']
PLATFORM_NAMES = {'doubao': '豆包', 'deepseek': 'DeepSeek', 'yuanbao': '元宝', 'qianwen': '千问'}
PLATFORM_URLS = {
    'doubao': 'https://www.doubao.com/chat/',
    'deepseek': 'https://chat.deepseek.com/',
    'yuanbao': 'https://yuanbao.tencent.com/',
    'qianwen': 'https://www.qianwen.com/',
}
