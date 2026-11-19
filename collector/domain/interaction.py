"""Interaction modes and the scope of the first desktop pilot."""

RPA_PLATFORMS = ('doubao', 'deepseek')
INTERACTION_MODES = ('browser', 'desktop_rpa')


def validate_interaction_mode(mode):
    if mode not in INTERACTION_MODES:
        raise ValueError('未知操作方式，请选择浏览器操作或桌面 RPA。')
    return mode
