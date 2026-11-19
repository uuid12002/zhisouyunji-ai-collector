"""Local batch selection and progress view; all persistence goes through the controller."""

from collector.domain.platforms import PLATFORM_NAMES
from .batch_report import STATUS
from .theme import card
import time

STAGES = {
    'queued': '等待执行',
    'checking_login': '检查登录',
    'waiting_login': '请在浏览器登录',
    'opening_chat': '新建对话',
    'configuring': '准备提问',
    'submitting': '发送问题',
    'generating': '等待回答',
    'capturing': '导出结果',
    'collecting': '采集中',
    'waiting_human': '请在浏览器完成验证',
    'waiting_overlay': '请关闭推广弹窗',
    'browser_error': '操作失败，浏览器已保留',
    'waiting_window': '请点击专用浏览器窗口',
    'resuming': '人工处理结束，继续原会话',
    'done': '结束',
    'stopped': '已停止',
    'cancelled': '已取消',
}


def batch_progress(state, now=None):
    """Keep the active platform separate from errors on already finished items."""
    base = f'{STATUS.get(state["status"], state["status"])} · 已处理 {state.get("finished", 0)} / {state["total"]}'
    active = next((i for i in state['items'] if i.get('status') == 'running'), None)
    if not active:
        return base
    name = PLATFORM_NAMES[active['platform']]
    stage = active.get('stage', '')
    stage_text = '等待人工验证' if stage == 'waiting_human' else STAGES.get(stage, stage)
    text = f'{base}\n当前：{name} · {stage_text}'
    waits = {
        'waiting_login': (600, '请手动登录并保留窗口。'),
        'waiting_human': (600, '请检查该平台验证码。'),
        'waiting_overlay': (120, '请关闭页面内推广弹窗。'),
        'waiting_window': (30, '请点击该平台专用窗口。'),
    }
    if stage in waits:
        limit, hint = waits[stage]
        started = active.get('stage_started_at')
        seconds = (
            max(0, int(limit - ((time.time() if now is None else now) - started)))
            if started
            else limit
        )
        text += f'\n剩余{seconds // 60:02d}:{seconds % 60:02d}，{hint}'
    return text


def batch_error(state, selected=None):
    errors = [i for i in state['items'] if i.get('error')]
    chosen = next((i for i in errors if i['id'] == selected), None)
    if chosen:
        return f'选中项：{PLATFORM_NAMES[chosen["platform"]]} · {chosen.get("code", "")}：{chosen["error"]}'[
            :400
        ]
    return f'{len(errors)}项异常，选中对应行查看原因。' if errors else ''


def show_batch_dialog(
    parent,
    controller,
    is_busy,
    launch,
    selected_platforms=(),
    interaction_mode='browser',
    *,
    embedded=False,
    on_login=None,
):
    import tkinter as tk
    from tkinter import ttk, messagebox

    dialog = ttk.Frame(parent, style='Page.TFrame') if embedded else tk.Toplevel(parent)
    if not embedded:
        from .theme import apply_theme

        apply_theme(dialog)
        dialog.title('批量采集 · 多平台 × 多问题')
        dialog.geometry('1180x780')
        dialog.minsize(1020, 700)
    panel = ttk.Frame(dialog, padding=4, style='Page.TFrame')
    panel.pack(fill='both', expand=True)
    panel.columnconfigure(0, weight=3)
    panel.columnconfigure(1, weight=2)
    panel.rowconfigure(0, weight=1)
    form = card(panel)
    form.grid(row=0, column=0, sticky='nsew', padx=(0, 14))
    side = card(panel)
    side.grid(row=0, column=1, sticky='nsew')
    top = ttk.Frame(form)
    top.pack(fill='x')
    ttk.Label(top, text='01  选择平台', style='Section.TLabel').pack(side='left')
    choices = ttk.Frame(form)
    choices.pack(fill='x', pady=(12, 8))
    catalog = controller.platform_catalog()
    available = {entry['id']: entry['name'] for entry in catalog if entry['available']}
    selections = {p: tk.BooleanVar(value=p in selected_platforms) for p in available}
    controls = []
    for platform, name in available.items():
        tile = ttk.Frame(choices, padding=5, relief='solid', borderwidth=1)
        tile.pack(side='left', fill='both', expand=True, padx=(0, 6))
        check = ttk.Checkbutton(tile, text=name, variable=selections[platform])
        check.pack(anchor='w')
        controls.append(check)
        if on_login:
            button = ttk.Button(
                tile, text='打开登录', padding=(6, 4), command=lambda p=platform: on_login(p)
            )
            button.pack(fill='x', pady=(5, 0))
            controls.append(button)
    select_all = ttk.Button(
        top, text='全选平台', command=lambda: [value.set(True) for value in selections.values()]
    )
    select_all.pack(side='right')
    controls.append(select_all)
    reserved_label = ttk.Label(form, style='Muted.TLabel', wraplength=560)
    reserved_label.pack(anchor='w', pady=(0, 8))

    def refresh_catalog():
        planned = [entry for entry in controller.platform_catalog() if not entry['available']]
        reserved_label.configure(
            text=f'已预留 {len(planned)} 个其他 AI 平台 · 待适配'
            if planned
            else '其他 AI 平台可在「平台登录与扩展」中预留。'
        )

    dialog.refresh_platform_catalog = refresh_catalog
    refresh_catalog()
    operation_row = ttk.Frame(form)
    operation_row.pack(fill='x', pady=6)
    ttk.Label(operation_row, text='操作方式').pack(side='left', padx=(0, 14))
    operations = {'浏览器操作': 'browser', '桌面 RPA（豆包 / DeepSeek）': 'desktop_rpa'}
    operation = tk.StringVar(value=next(k for k, v in operations.items() if v == interaction_mode))
    operation_selector = ttk.Combobox(
        operation_row, textvariable=operation, values=list(operations), state='readonly', width=24
    )
    operation_selector.pack(side='left', fill='x', expand=True)
    ttk.Label(operation_row, text='思考模式').pack(side='left', padx=(12, 8))
    thinking_modes = {'平台默认': None, '开启': True, '关闭': False}
    thinking = tk.StringVar(value='平台默认')
    thinking_selector = ttk.Combobox(
        operation_row,
        textvariable=thinking,
        values=list(thinking_modes),
        state='readonly',
        width=10,
    )
    thinking_selector.pack(side='left')
    ttk.Label(
        form,
        text='RPA 占用键鼠，请勿切换窗口；元宝、千问使用浏览器。',
        foreground='#A4671F',
        wraplength=590,
    ).pack(anchor='w', pady=4)
    mode = tk.StringVar(value='每行一个问题')
    question_heading = ttk.Frame(form)
    question_heading.pack(fill='x', pady=(10, 6))
    ttk.Label(question_heading, text='02  添加问题', style='Section.TLabel').pack(side='left')
    modes = ttk.Combobox(
        question_heading,
        textvariable=mode,
        values=['每行一个问题', '空行分隔多行问题'],
        state='readonly',
        width=20,
    )
    modes.pack(side='right')
    ttk.Label(form, text='最多 100 个问题，每题最多 10000 字。', style='Muted.TLabel').pack(
        anchor='w', pady=3
    )
    editor_box = ttk.Frame(form)
    editor_box.pack(fill='both', expand=True)
    editor = tk.Text(
        editor_box,
        height=3,
        wrap='word',
        font=('Microsoft YaHei UI', 11),
        background='#FAFBFE',
        foreground='#192333',
        insertbackground='#3264ED',
        relief='solid',
        borderwidth=1,
        padx=12,
        pady=12,
        highlightthickness=0,
        spacing1=4,
        spacing3=5,
        undo=True,
    )
    editor.pack(side='left', fill='both', expand=True)
    editor_scroll = ttk.Scrollbar(editor_box, command=editor.yview)
    editor_scroll.pack(side='right', fill='y')
    editor.configure(yscrollcommand=editor_scroll.set)
    summary = tk.StringVar(value='勾选平台并填写问题。')
    outputs = ttk.Frame(form)
    outputs.pack(side='bottom', fill='x', before=top)
    ttk.Label(outputs, textvariable=summary, style='Muted.TLabel').pack(anchor='w', pady=6)
    ttk.Separator(outputs).pack(fill='x', pady=6)
    ttk.Label(outputs, text='03  保存内容', style='Section.TLabel').pack(anchor='w', pady=(4, 8))
    ttk.Label(outputs, text='✓ 回复全文 TXT    ✓ 回答长图 PNG    ✓ 会话分享链接').pack(anchor='w')
    ttk.Label(
        outputs,
        text='回答完成后导出，分享链接通过页面分享按钮复制。',
        style='Muted.TLabel',
        wraplength=590,
    ).pack(anchor='w', pady=6)
    side_actions = ttk.Frame(side)
    side_actions.pack(side='bottom', fill='x')
    ttk.Label(side, text='执行预览', style='Section.TLabel').pack(anchor='w')
    ttk.Label(side, text='顺序执行 · 同一时间只执行一项任务', style='Muted.TLabel').pack(
        anchor='w', pady=(6, 10)
    )
    progress = tk.StringVar(value='遇到验证时等待人工处理。RPA 运行时按 Esc 可在下一次输入前停止。')
    progress_box = ttk.Frame(side, height=80)
    progress_box.pack(fill='x', pady=5)
    progress_box.pack_propagate(False)
    progress_label = ttk.Label(progress_box, textvariable=progress, wraplength=270, anchor='nw')
    progress_label.pack(fill='both', expand=True)
    progress_label.bind(
        '<Configure>', lambda event: progress_label.configure(wraplength=max(180, event.width))
    )
    error_details = tk.StringVar(value='')
    error_box = ttk.Frame(side, height=42)
    error_box.pack(fill='x', pady=3)
    error_box.pack_propagate(False)
    error_label = ttk.Label(
        error_box, textvariable=error_details, foreground='firebrick', wraplength=270, anchor='nw'
    )
    error_label.pack(fill='both', expand=True)
    error_label.bind(
        '<Configure>', lambda event: error_label.configure(wraplength=max(180, event.width))
    )
    columns = ('number', 'platform', 'status', 'stage', 'question')
    table_frame = ttk.Frame(side)
    table_frame.pack(fill='both', expand=True)
    table = ttk.Treeview(
        table_frame,
        columns=columns,
        show='headings',
        height=7,
        displaycolumns=('number', 'platform', 'status', 'stage'),
    )
    for column, title, width in [
        ('number', '题号', 40),
        ('platform', '平台', 70),
        ('status', '状态', 60),
        ('stage', '进度', 110),
        ('question', '问题', 200),
    ]:
        table.heading(column, text=title)
        table.column(column, width=width, minwidth=40, stretch=column == 'stage')
    table.pack(side='left', fill='both', expand=True)
    scroll = ttk.Scrollbar(table_frame, orient='vertical', command=table.yview)
    scroll.pack(side='right', fill='y')
    table.configure(yscrollcommand=scroll.set)
    folder = None
    process = None
    latest_state = None
    poll_jobs = []

    def selection_details(event=None):
        if latest_state:
            selected = table.selection()
            error_details.set(batch_error(latest_state, selected[0] if selected else None))

    table.bind('<<TreeviewSelect>>', selection_details)

    def schedule_poll(delay):
        poll_jobs.append(dialog.after(delay, poll))

    def release_timers(event):
        if event.widget is dialog:
            for job in poll_jobs:
                dialog.after_cancel(job)

    dialog.bind('<Destroy>', release_timers, add='+')
    buttons = ttk.Frame(form)
    buttons.pack(side='bottom', fill='x', pady=(12, 0), before=outputs)

    def inputs():
        return (
            [p for p, value in selections.items() if value.get()],
            editor.get('1.0', 'end'),
            'lines' if mode.get() == '每行一个问题' else 'blocks',
            operations[operation.get()],
            thinking_modes[thinking.get()],
        )

    def preview(*args):
        if folder is not None:
            return
        try:
            request = controller.preview_local_batch(*inputs())
            count = len(request['questions'])
            platforms = len(request['platforms'])
            summary.set(
                f'{count} 个问题 × {platforms} 个平台 = {count * platforms} 项采集 · 思考：{thinking.get()}'
            )
            from collector.domain.batches import batch_items

            table.delete(*table.get_children())
            for item in batch_items(request['platforms'], request['questions']):
                table.insert(
                    '',
                    'end',
                    iid=item['id'],
                    values=(
                        item['question_index'],
                        PLATFORM_NAMES[item['platform']],
                        '待执行',
                        '等待启动',
                        item['question'],
                    ),
                )
        except ValueError as exc:
            summary.set(str(exc))
            table.delete(*table.get_children())

    for value in selections.values():
        value.trace_add('write', preview)
    mode.trace_add('write', preview)
    operation.trace_add('write', preview)
    thinking.trace_add('write', preview)
    editor.bind('<KeyRelease>', preview)

    def modified(event=None):
        if editor.edit_modified():
            editor.edit_modified(False)
            preview()

    editor.bind('<<Modified>>', modified)

    def poll():
        nonlocal latest_state
        if not dialog.winfo_exists():
            return
        state = None
        try:
            state = controller.local_status(folder)
            if state:
                latest_state = state
                progress.set(batch_progress(state))
                selection_details()
                for item in state['items']:
                    values = (
                        item['question_index'],
                        PLATFORM_NAMES[item['platform']],
                        STATUS.get(item['status'], item['status']),
                        STAGES.get(item['stage'], item['stage']),
                        item['question'].replace('\n', ' ')[:100],
                    )
                    if table.exists(item['id']):
                        table.item(item['id'], values=values)
                    else:
                        table.insert('', 'end', iid=item['id'], values=values)
                if not table.selection():
                    active = next((i for i in state['items'] if i.get('status') == 'running'), None)
                    if active:
                        table.see(active['id'])
        except (OSError, ValueError):
            pass
        if process and process.poll() is None:
            schedule_poll(500)
        else:
            stop_button.configure(state='disabled')
            if not state or state['status'] in ('running', 'interrupted'):
                progress.set('采集进程已退出，请查看汇总和诊断目录；本批次不会自动重发。')

    def start():
        nonlocal folder, process
        if is_busy():
            messagebox.showinfo('设备忙', '请先停止其他采集或登录操作。', parent=dialog)
            return
        try:
            folder = controller.create_local_batch(*inputs())
            process = launch(folder)
        except Exception as exc:
            messagebox.showerror('启动失败', str(exc), parent=dialog)
            return
        run_button.configure(state='disabled')
        editor.configure(state='disabled')
        for widget in controls:
            widget.configure(state='disabled')
        modes.configure(state='disabled')
        operation_selector.configure(state='disabled')
        thinking_selector.configure(state='disabled')
        stop_button.configure(state='normal')
        result_button.configure(state='normal')
        progress.set('正在启动批量采集…')
        schedule_poll(300)

    def stop():
        controller.stop_local_batch(folder)
        stop_button.configure(state='disabled')
        progress.set('已请求停止后续任务，等待当前回答和导出结束。')

    def open_item(event=None):
        selected = table.selection()
        if folder and selected:
            item = next(
                (i for i in (latest_state or {}).get('items', []) if i['id'] == selected[0]), None
            )
            if item and item.get('status') == 'failed':
                messagebox.showinfo(
                    '失败项详情', batch_error(latest_state, selected[0]), parent=dialog
                )
                return
            controller.open_batch_item(folder, selected[0])

    run_button = ttk.Button(buttons, text='开始批量采集', command=start, style='Primary.TButton')
    run_button.pack(side='right')
    clear_button = ttk.Button(buttons, text='清空问题', command=lambda: editor.delete('1.0', 'end'))
    clear_button.pack(side='left')
    controls.append(clear_button)
    stop_button = ttk.Button(side_actions, text='停止后续任务', command=stop, state='disabled')
    stop_button.pack(fill='x', pady=(12, 6))
    result_button = ttk.Button(
        side_actions,
        text='打开批量汇总',
        command=lambda: controller.open_result(folder),
        state='disabled',
    )
    result_button.pack(fill='x', pady=4)
    ttk.Button(side_actions, text='打开选中项结果', command=open_item).pack(fill='x', pady=4)

    def new_batch():
        nonlocal folder, process
        if process and process.poll() is None:
            messagebox.showinfo('正在采集', '请先等待当前批次结束。', parent=dialog)
            return
        folder, process = None, None
        editor.configure(state='normal')
        for widget in controls:
            widget.configure(state='normal')
        modes.configure(state='readonly')
        operation_selector.configure(state='readonly')
        thinking_selector.configure(state='readonly')
        run_button.configure(state='normal')
        result_button.configure(state='disabled')
        progress.set('填写新问题后点击开始；不会自动重发。')
        error_details.set('')
        preview()

    ttk.Button(side_actions, text='新建下一批次', command=new_batch).pack(fill='x', pady=(8, 0))
    table.bind('<Double-1>', open_item)

    def close():
        if process and process.poll() is None:
            messagebox.showinfo(
                '采集进行中', '可先停止后续任务，等待当前任务结束后关闭。', parent=dialog
            )
        else:
            dialog.destroy()

    if not embedded:
        dialog.protocol('WM_DELETE_WINDOW', close)
    preview()
    return dialog
