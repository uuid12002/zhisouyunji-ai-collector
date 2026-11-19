"""Render escaped HTML; persistence belongs to the local store."""

import html
import json
from pathlib import Path
from urllib.parse import urlparse


def link(url, title):
    try:
        parsed = urlparse(url or '')
        valid = (
            parsed.scheme in ('http', 'https')
            and parsed.hostname
            and (not parsed.username)
            and (not parsed.password)
        )
    except ValueError:
        valid = False
    return (
        f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(title)}</a>'
        if valid
        else html.escape(title + '（未提供有效网址）')
    )


def render_report(question, result):
    actual = result.get('actual_options', {})
    thinking = actual.get('thinking_mode')
    thinking_label = (
        '开启' if thinking is True else '关闭' if thinking is False else '平台默认（未核验）'
    )
    parts = [
        '<!doctype html><meta charset="utf-8"><title>本机采集结果</title><style>body{max-width:1000px;margin:32px auto;padding:20px;font:16px/1.7 system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere}img{max-width:100%;border:1px solid #ddd}a{overflow-wrap:anywhere}</style>',
        '<h1>本机采集结果</h1><p>仅保存在本机，未上传服务器。</p><h2>问题</h2><pre>'
        + html.escape(question)
        + '</pre>',
        '<p>操作方式：'
        + ('桌面 RPA（试用）' if result.get('interaction_mode') == 'desktop_rpa' else '浏览器操作')
        + '</p>',
        '<p>思考模式：'
        + thinking_label
        + ' · '
        + html.escape(actual.get('thinking_mode_status', '未记录'))
        + '</p>',
        '<h2>分享链接</h2>' + link(result.get('share_url'), '打开页面分享按钮生成的链接'),
        '<p><button id="copy-answer">复制全部回复</button> <button id="copy-share">复制分享链接</button> <span id="copy-status"></span></p>',
        '<h2>网页回答正文</h2><pre>' + html.escape(result['answer']) + '</pre><h2>参考链接</h2>',
    ]
    for ref in result.get('references', []):
        label = {'cited': '正文引用', 'search_result': '搜索候选'}.get(
            ref.get('relation'), '其他链接'
        )
        parts.append(
            '<p>'
            + html.escape('[' + label + '] ')
            + link(ref.get('displayed_url'), ref.get('title') or ref.get('displayed_url') or '来源')
            + '</p>'
        )
    if not result.get('references'):
        parts.append('<p>本次未提取到参考来源。</p>')
    parts.append('<h2>网页截图</h2>')
    for item in result.get('files', []):
        name = item['name']
        if Path(name).name != name or '/' in name or '\\' in name:
            raise ValueError('非法证据文件名')
        if item['mime'] == 'image/png' and (
            item.get('kind') == 'answer_long_image' or not result.get('long_image_complete')
        ):
            parts.append(
                '<p>'
                + html.escape(name)
                + '</p><img src="'
                + html.escape(name, quote=True)
                + '" alt="真实网页截图">'
            )
    payload = json.dumps(
        {'answer': result['answer'], 'share_url': result.get('share_url')}, ensure_ascii=True
    ).replace('<', '\\u003c')
    parts.append(
        '<script>const result='
        + payload
        + ";async function copy(value){const status=document.getElementById('copy-status');if(!value){status.textContent='未取得分享链接';return;}try{await navigator.clipboard.writeText(value);status.textContent='已复制';}catch{const field=document.createElement('textarea');field.value=value;document.body.append(field);field.select();const ok=document.execCommand('copy');field.remove();status.textContent=ok?'已复制':'复制失败，请手动复制正文或链接';}}document.getElementById('copy-answer').onclick=()=>copy(result.answer);document.getElementById('copy-share').onclick=()=>copy(result.share_url);</script>"
    )
    for warning in result.get('limitations', []):
        parts.append('<p>' + html.escape(warning) + '</p>')
    return '\n'.join(parts)
