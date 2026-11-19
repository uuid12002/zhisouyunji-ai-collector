"""Platform discovery and planned integrations, independent of UI and files."""

import re
from urllib.parse import urlparse
from collector.domain.platforms import PLATFORM_NAMES, PLATFORM_URLS
from collector.domain.interaction import RPA_PLATFORMS


def platform_catalog(planned):
    return [
        {
            'id': key,
            'name': name,
            'url': PLATFORM_URLS[key],
            'available': True,
            'modes': ['browser', 'desktop_rpa'] if key in RPA_PLATFORMS else ['browser'],
        }
        for key, name in PLATFORM_NAMES.items()
    ] + [validate_planned(value) for value in planned]


def validate_planned(value):
    key, name, url = (str(value.get(k, '')).strip() for k in ('id', 'name', 'url'))
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,39}', key) or key in PLATFORM_NAMES:
        raise ValueError('平台编号须为 2～40 位小写字母、数字或下划线，且不能覆盖现有平台')
    if not 1 <= len(name) <= 24 or any(ord(c) < 32 for c in name):
        raise ValueError('平台名称须为 1～24 字')
    parsed = urlparse(url)
    if (
        len(url) > 500
        or parsed.scheme != 'https'
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or any(c.isspace() for c in url)
    ):
        raise ValueError('请输入有效的 HTTPS 平台地址，不含账号或密码')
    return {'id': key, 'name': name, 'url': url, 'available': False, 'modes': []}
