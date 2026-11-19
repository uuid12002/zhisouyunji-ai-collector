"""Escaped batch summary with links to independently saved results."""

import html
import re
from collector.domain.platforms import PLATFORM_NAMES

STATUS = {
    'queued': '排队中',
    'running': '采集中',
    'completed': '完成',
    'partial': '部分完成',
    'failed': '失败',
    'skipped': '跳过',
    'cancelled': '已取消',
    'stopped': '已停止',
    'interrupted': '已中断',
}


def render_batch_report(state):
    parts = [
        '<!doctype html><meta charset="utf-8"><title>批量采集结果</title>',
        '<style>body{font:16px/1.7 system-ui;margin:32px}table{border-collapse:collapse;width:100%}td,th{padding:10px;border:1px solid #ddd;text-align:left}td{white-space:pre-wrap;overflow-wrap:anywhere}</style>',
        '<h1>批量采集结果</h1>',
        '<p>'
        + html.escape(STATUS.get(state['status'], state['status']))
        + f' · 已处理 {state.get("finished", 0)} / {state["total"]}</p>',
        '<table><tr><th>问题</th><th>平台</th><th>操作方式</th><th>思考模式（请求）</th><th>状态</th><th>结果</th></tr>',
    ]
    for item in state['items']:
        link = ''
        if re.fullmatch(r'\d{4}-(doubao|deepseek|yuanbao|qianwen)', item['id']):
            link = (
                '<a href="items/' + item['id'] + '/report.html">打开结果</a>'
                if item.get('report')
                else '<a href="items/' + item['id'] + '/">查看诊断目录</a>'
                if item['status'] == 'failed'
                else ''
            )
        error = '<br>' + html.escape(item['error']) if item.get('error') else ''
        parts.append(
            '<tr><td>'
            + str(item['question_index'])
            + '. '
            + html.escape(item['question'])
            + '</td><td>'
            + html.escape(PLATFORM_NAMES.get(item['platform'], item['platform']))
            + '</td><td>'
            + (
                '桌面 RPA（试用）'
                if item.get('interaction_mode') == 'desktop_rpa'
                else '浏览器操作'
            )
            + '</td><td>'
            + (
                '开启'
                if item.get('thinking_mode') is True
                else '关闭'
                if item.get('thinking_mode') is False
                else '平台默认'
            )
            + '</td><td>'
            + html.escape(STATUS.get(item['status'], item['status']))
            + error
            + '</td><td>'
            + link
            + '</td></tr>'
        )
    parts.append(
        '</table><p>未取得的长图或分享链接请查看各项结果说明。失败和跳过的项目不会自动重新提问。</p>'
    )
    return '\n'.join(parts)
