"""Requested thinking setting: None preserves the platform's current mode."""


def validate_thinking_mode(value):
    if value is not None and type(value) is not bool:
        raise ValueError('思考模式必须为平台默认、开启或关闭')
    return value
