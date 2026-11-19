"""Connection validation has no UI or filesystem side effects."""

from urllib.parse import urlparse
from collector.domain.platforms import PLATFORM_NAMES


def validate_config(config):
    config = dict(config)
    parsed = urlparse(config['server_url'])
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError('服务器端口必须是 1～65535 的数字') from exc
    if port is not None and (not 1 <= port <= 65535):
        raise ValueError('服务器端口必须是 1～65535 的数字')
    if (
        parsed.scheme not in ('https', 'http')
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (parsed.path not in ('', '/'))
    ):
        raise ValueError('服务器地址必须为 https://域名:端口，不包含路径或密码')
    if parsed.scheme != 'https' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('远程服务器必须使用 HTTPS；HTTP 仅用于本机测试')
    if not config.get('device_id') or len(config.get('device_key', '')) < 24:
        raise ValueError('请填写服务端签发的设备编号和密钥')
    if not config.get('platforms') or any((p not in PLATFORM_NAMES for p in config['platforms'])):
        raise ValueError('至少启用一个已登录的平台')
    config['server_url'] = config['server_url'].rstrip('/')
    config['slots'] = int(config.get('slots', 1))
    if config['slots'] not in (1, 2):
        raise ValueError('并发数量只能是 1 或 2')
    return config
