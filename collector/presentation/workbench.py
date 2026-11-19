"""Desktop workspace pages. OS and storage operations belong to the controller."""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from datetime import datetime
from collector.domain.platforms import PLATFORM_NAMES
from collector.version import VERSION
from .theme import apply_theme, card, BLUE, MUTED
from .batch_dialog import show_batch_dialog
from .batch_report import STATUS


class Workbench:
    def __init__(self, controller, autostart=False, smoke_test=False):
        self.controller = controller
        self.window = tk.Tk()
        self.window.title(f'网页采集工作台 v{VERSION}')
        self.window.geometry('1320x840')
        self.window.minsize(1180, 840)
        apply_theme(self.window)
        self.processes = {'worker': None, 'login': None, 'local': None}
        self.browser_launchers = []
        self.active_run = None
        self.pages, self.nav, self.current = {}, {}, None
        self.status = tk.StringVar(value='就绪 · 本机保存 · 本机批次顺序执行')
        try:
            self.config = controller.load_configuration()
        except (ValueError, OSError) as exc:
            self.config = {}
            messagebox.showerror('配置读取失败', str(exc), parent=self.window)
        self.window.columnconfigure(1, weight=1)
        self.window.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(self.window, padding=(16, 26), style='Page.TFrame', width=194)
        sidebar.grid(row=0, column=0, sticky='ns')
        sidebar.pack_propagate(False)
        ttk.Label(
            sidebar, text='▤', foreground=BLUE, font=('Segoe UI', 34), style='Page.TLabel'
        ).pack(anchor='w')
        ttk.Label(
            sidebar,
            text='网页采集工作台',
            font=('Microsoft YaHei UI', 14, 'bold'),
            style='Page.TLabel',
        ).pack(anchor='w', pady=(6, 3))
        ttk.Label(sidebar, text=f'v{VERSION}', foreground=MUTED, style='Page.TLabel').pack(
            anchor='w', pady=(0, 28)
        )
        for key, name in [
            ('collect', '批量采集'),
            ('history', '任务记录'),
            ('platforms', '平台登录与扩展'),
            ('agent', '智能体接入'),
            ('connection', '连接设置'),
        ]:
            button = ttk.Button(
                sidebar, text=name, style='Nav.TButton', command=lambda k=key: self.show(k)
            )
            button.pack(fill='x', pady=3)
            self.nav[key] = button
        ttk.Button(sidebar, text='日志与文件', command=controller.open_home).pack(
            side='bottom', fill='x'
        )
        content = ttk.Frame(self.window, padding=(20, 22, 22, 12), style='Page.TFrame')
        content.grid(row=0, column=1, sticky='nsew')
        self.title = ttk.Label(content, style='Title.TLabel')
        self.title.pack(anchor='w')
        self.subtitle = ttk.Label(content, style='Page.TLabel', foreground=MUTED)
        self.subtitle.pack(anchor='w', pady=(5, 18))
        self.body = ttk.Frame(content, style='Page.TFrame')
        self.body.pack(fill='both', expand=True)
        ttk.Label(self.window, textvariable=self.status, style='Page.TLabel', padding=(20, 8)).grid(
            row=1, column=0, columnspan=2, sticky='ew'
        )
        self.show('collect')
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.poll_job = self.window.after(1000, self.poll)
        self.window.bind('<Destroy>', self.on_destroy, add='+')
        if autostart and self.config.get('auto_start') is True:
            self.show('connection')
            self.window.after(500, self.start_worker)
        if smoke_test:
            self.window.withdraw()
            self.window.after(150, self.window.destroy)

    def busy(self):
        return any(
            p and p.poll() is None for p in [*self.processes.values(), *self.browser_launchers]
        )

    def spawn(self, kind, *args):
        process, run_id = self.controller.spawn(*args)
        self.processes[kind] = process
        if kind == 'login':
            self.browser_launchers.append(process)
        if kind == 'worker':
            self.active_run = run_id
        return process

    def login(self, platform):
        if self.busy():
            messagebox.showinfo('设备忙', '请等待当前采集或浏览器启动完成。', parent=self.window)
            return
        try:
            self.spawn('login', '--login', platform)
            self.status.set('正在打开或唤起专用浏览器；登录后无需关闭，直接开始采集。')
        except Exception as exc:
            messagebox.showerror('登录窗口启动失败', str(exc), parent=self.window)

    def show(self, key):
        if self.current:
            self.pages[self.current].pack_forget()
        titles = {
            'collect': ('新建采集任务', '选择平台，添加问题，统一保存采集结果。'),
            'history': ('任务记录', '查看已保存的回答、长图及页面分享链接。'),
            'platforms': ('平台登录与扩展', '登录已适配平台，也可以为其他 AI 平台预留接入位置。'),
            'agent': ('智能体接入', '通过本机 HTTP 或 MCP 提交采集任务。'),
            'connection': ('连接设置', '配置远程任务服务；本机批量采集无需填写服务器配置。'),
        }
        self.title.configure(text=titles[key][0])
        self.subtitle.configure(text=titles[key][1])
        if key not in self.pages:
            if key == 'collect':
                self.pages[key] = show_batch_dialog(
                    self.body,
                    self.controller,
                    self.busy,
                    lambda folder: self.spawn(
                        'local', '--local-batch', str(folder / 'request.json')
                    ),
                    ['doubao', 'deepseek'],
                    'desktop_rpa',
                    embedded=True,
                    on_login=self.login,
                )
            else:
                page = ttk.Frame(self.body, style='Page.TFrame')
                self.pages[key] = page
                getattr(self, 'build_' + key)(page)
        self.pages[key].pack(fill='both', expand=True)
        self.current = key
        for name, button in self.nav.items():
            button.configure(style='Selected.Nav.TButton' if name == key else 'Nav.TButton')
        if key == 'history':
            self.refresh_history()
        if key == 'collect':
            self.pages[key].refresh_platform_catalog()

    def build_history(self, parent):
        area = card(parent, '最近 200 项采集')
        area.pack(fill='both', expand=True)
        ttk.Button(area, text='刷新记录', command=self.refresh_history).pack(
            anchor='e', pady=(0, 8)
        )
        box = ttk.Frame(area)
        box.pack(fill='both', expand=True)
        columns = ('time', 'platform', 'status', 'question')
        self.history = ttk.Treeview(box, columns=columns, show='headings')
        for key, title, width in [
            ('time', '最近更新', 140),
            ('platform', '平台', 100),
            ('status', '状态', 100),
            ('question', '问题', 440),
        ]:
            self.history.heading(key, text=title)
            self.history.column(key, width=width, stretch=key == 'question')
        self.history.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(box, command=self.history.yview)
        scroll.pack(side='right', fill='y')
        self.history.configure(yscrollcommand=scroll.set)
        self.history_rows = {}
        self.history_empty = ttk.Label(area, style='Muted.TLabel')
        self.history_empty.pack(anchor='w', pady=10)
        actions = ttk.Frame(area)
        actions.pack(fill='x', pady=6)
        for name, kind in [
            ('查看全文', 'answer'),
            ('打开长图', 'image'),
            ('复制分享链接', 'share'),
            ('单项报告', 'report'),
        ]:
            ttk.Button(actions, text=name, command=lambda k=kind: self.open_saved(k)).pack(
                side='left', padx=(0, 8)
            )
        self.history.bind('<Double-1>', lambda e: self.open_saved('report'))

    def refresh_history(self):
        try:
            entries = self.controller.recent_collections()
            self.history.delete(*self.history.get_children())
            self.history_rows = {}
            for index, item in enumerate(entries):
                key = str(index)
                self.history_rows[key] = item
                self.history.insert(
                    '',
                    'end',
                    iid=key,
                    values=(
                        datetime.fromtimestamp(item['updated']).strftime('%m-%d %H:%M'),
                        PLATFORM_NAMES.get(item['platform'], item['platform']),
                        STATUS.get(item['status'], item['status']),
                        item['question'].replace('\n', ' ')[:110],
                    ),
                )
            self.history_empty.configure(
                text='暂无采集记录，完成首批任务后会显示在这里。'
                if not entries
                else '选择记录后，可打开全文、长图或复制页面分享链接。'
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror('读取记录失败', str(exc), parent=self.window)

    def open_saved(self, kind):
        selected = self.history.selection()
        if not selected:
            messagebox.showinfo('请选择记录', '先选择一项采集记录。', parent=self.window)
            return
        if self.busy():
            messagebox.showinfo(
                '正在采集', '请等待采集结束，避免切换窗口或改动剪贴板。', parent=self.window
            )
            return
        item = self.history_rows[selected[0]]
        try:
            value = self.controller.saved_output(Path(item['folder']), item['id'], kind)
            if kind == 'share':
                self.window.clipboard_clear()
                self.window.clipboard_append(value)
                self.status.set('已复制通过页面分享按钮取得的会话链接。')
        except (ValueError, OSError) as exc:
            messagebox.showinfo('结果暂不可用', str(exc), parent=self.window)

    def build_platforms(self, parent):
        header = card(parent)
        header.pack(fill='x', pady=(0, 14))
        ttk.Label(header, text='平台接入管理', style='Section.TLabel').pack(side='left')
        ttk.Button(
            header,
            text='+ 预留其他 AI 平台',
            style='Primary.TButton',
            command=self.reserve_platform,
        ).pack(side='right')
        self.platform_list = ttk.Frame(parent)
        ttk.Button(header, text='打开全部浏览器', command=self.open_all_browsers).pack(
            side='right', padx=12
        )
        self.platform_list.pack(fill='both', expand=True)
        self.refresh_platforms()

    def refresh_platforms(self):
        for child in self.platform_list.winfo_children():
            child.destroy()
        self.platform_rows = {}
        try:
            entries = self.controller.platform_catalog()
        except (ValueError, OSError) as exc:
            messagebox.showerror('平台配置读取失败', str(exc), parent=self.window)
            return
        box = card(self.platform_list)
        box.pack(fill='both', expand=True)
        table_box = ttk.Frame(box)
        table_box.pack(fill='both', expand=True)
        self.platform_table = ttk.Treeview(
            table_box, columns=('name', 'mode', 'session', 'url'), show='headings'
        )
        for key, label, width in [
            ('name', '平台', 140),
            ('mode', '接入状态', 210),
            ('session', '浏览器', 130),
            ('url', '官网地址', 350),
        ]:
            self.platform_table.heading(key, text=label)
            self.platform_table.column(key, width=width, stretch=key == 'url')
        self.platform_table.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(table_box, command=self.platform_table.yview)
        scroll.pack(side='right', fill='y')
        self.platform_table.configure(yscrollcommand=scroll.set)
        sessions = self.controller.browser_sessions()
        for entry in entries:
            state = (
                ('已适配 · 浏览器 / RPA' if 'desktop_rpa' in entry['modes'] else '已适配 · 浏览器')
                if entry['available']
                else '待适配 · 不可执行'
            )
            self.platform_table.insert(
                '',
                'end',
                iid=entry['id'],
                values=(entry['name'], state, sessions.get(entry['id'], '待适配'), entry['url']),
            )
            self.platform_rows[entry['id']] = entry
        actions = ttk.Frame(box)
        actions.pack(fill='x', pady=16)
        self.platform_login = ttk.Button(
            actions, text='打开 / 唤起浏览器', command=self.login_selected, state='disabled'
        )
        self.platform_login.pack(side='left', padx=(0, 10))
        self.platform_close = ttk.Button(
            actions, text='关闭选中浏览器', command=self.close_selected_browser, state='disabled'
        )
        self.platform_close.pack(side='left', padx=(0, 10))
        self.platform_remove = ttk.Button(
            actions, text='移除待适配平台', command=self.remove_selected, state='disabled'
        )
        self.platform_remove.pack(side='left')
        self.platform_table.bind('<<TreeviewSelect>>', self.select_platform)
        ttk.Label(
            box,
            text='浏览器以【采集专用 · 平台名】标识，登录与任务完成后均保持打开。关闭主程序不会关闭这些窗口。预留平台完成适配后才开放执行。',
            style='Muted.TLabel',
            wraplength=850,
        ).pack(anchor='w', pady=8)

    def select_platform(self, event=None):
        selected = self.platform_table.selection()
        entry = self.platform_rows[selected[0]] if selected else None
        self.platform_login.configure(
            state='normal' if entry and entry['available'] else 'disabled'
        )
        self.platform_remove.configure(
            state='normal' if entry and not entry['available'] else 'disabled'
        )
        self.platform_close.configure(
            state='normal' if entry and entry['available'] else 'disabled'
        )

    def open_all_browsers(self):
        if self.busy():
            messagebox.showinfo('设备忙', '请等待当前任务或窗口启动完成。', parent=self.window)
            return
        for platform in PLATFORM_NAMES:
            self.spawn('login', '--login', platform)
        self.status.set('正在打开四个平台的独立常驻浏览器；已打开的平台将复用窗口。')

    def close_selected_browser(self):
        selected = self.platform_table.selection()
        if not selected or not self.platform_rows[selected[0]]['available']:
            return
        if self.busy():
            messagebox.showinfo('设备忙', '请等待当前任务完成后再关闭浏览器。', parent=self.window)
            return
        self.spawn('login', '--close-browser', selected[0])
        self.status.set('已请求关闭选中平台浏览器；执行任务期间会拒绝关闭。')

    def login_selected(self):
        selected = self.platform_table.selection()
        if selected and self.platform_rows[selected[0]]['available']:
            self.login(selected[0])

    def remove_selected(self):
        selected = self.platform_table.selection()
        if selected and not self.platform_rows[selected[0]]['available']:
            self.controller.remove_reserved_platform(selected[0])
            self.refresh_platforms()

    def reserve_platform(self):
        dialog = tk.Toplevel(self.window)
        dialog.title('预留其他 AI 平台')
        dialog.geometry('500x450')
        dialog.transient(self.window)
        form = card(dialog, '添加待适配平台')
        form.pack(fill='both', expand=True, padx=12, pady=12)
        ttk.Label(form, text='仅保存接入信息，不会打开网站或启用采集。', style='Muted.TLabel').pack(
            anchor='w', pady=(0, 12)
        )
        values = {}
        for key, label in [
            ('name', '平台名称'),
            ('id', '平台编号（小写英文，如 my_ai）'),
            ('url', '官网地址（HTTPS）'),
        ]:
            ttk.Label(form, text=label).pack(anchor='w')
            values[key] = tk.StringVar()
            ttk.Entry(form, textvariable=values[key]).pack(fill='x', pady=(4, 10))

        def save():
            try:
                self.controller.reserve_platform(
                    {key: value.get() for key, value in values.items()}
                )
                self.refresh_platforms()
                self.status.set('已预留平台，完成适配后才能开放采集。')
                dialog.destroy()
            except (ValueError, OSError) as exc:
                messagebox.showerror('保存失败', str(exc), parent=dialog)

        ttk.Button(form, text='保存预留', command=save, style='Primary.TButton').pack(
            anchor='e', pady=(8, 0)
        )

    def build_agent(self, parent):
        area = card(parent, '让智能体调用采集能力')
        area.pack(fill='x')
        ttk.Label(
            area,
            text='HTTP / MCP → 任务队列 → 本机采集 → 全文、长图、分享链接',
            font=('Microsoft YaHei UI', 12),
        ).pack(anchor='w', pady=12)
        ttk.Label(
            area,
            text='默认本机端口 18765，使用 Bearer 密钥。连接配置为 agent-connection.json。\nMCP 客户端配置 WebCollectorMCP.exe；启动失败请查看 client.log。\n接口独立运行，关闭本窗口不会停止接口。',
            wraplength=850,
            style='Muted.TLabel',
        ).pack(anchor='w', pady=12)
        actions = ttk.Frame(area)
        actions.pack(fill='x', pady=12)
        for text, flag, primary in [
            ('启动智能体接口', '--agent-server', True),
            ('停止接口（等待当前任务）', '--agent-stop', False),
        ]:
            ttk.Button(
                actions,
                text=text,
                style='Primary.TButton' if primary else 'TButton',
                command=lambda f=flag: self.agent_action(f),
            ).pack(side='left', padx=(0, 12))
        ttk.Button(area, text='打开日志与连接配置目录', command=self.controller.open_home).pack(
            anchor='w'
        )

    def agent_action(self, flag):
        try:
            self.controller.spawn(flag)
            self.status.set(
                '已请求启动本机接口，启动失败请查看 client.log。'
                if flag == '--agent-server'
                else '已请求停止接口，等待当前任务结束。'
            )
        except Exception as exc:
            messagebox.showerror('接口操作失败', str(exc), parent=self.window)

    def build_connection(self, parent):
        area = card(parent, '手机 / 电脑统一采集管理后台')
        area.pack(fill='both', expand=True)
        ttk.Button(area, text='导入管理员提供的配置', command=self.import_config).pack(
            anchor='w', pady=(0, 12)
        )
        self.connection_values = {}
        for key, label in [
            ('server_url', '服务器地址（HTTPS，可带端口）'),
            ('device_id', '设备编号'),
            ('device_key', '设备密钥'),
        ]:
            ttk.Label(area, text=label).pack(anchor='w')
            value = tk.StringVar(value=self.config.get(key, ''))
            self.connection_values[key] = value
            ttk.Entry(area, textvariable=value, show='*' if key == 'device_key' else '').pack(
                fill='x', pady=(4, 12)
            )
        choices = ttk.Frame(area)
        choices.pack(fill='x', pady=8)
        self.worker_platforms = {
            key: tk.BooleanVar(value=key in self.config.get('platforms', ['doubao']))
            for key in PLATFORM_NAMES
        }
        for key, value in self.worker_platforms.items():
            ttk.Checkbutton(choices, text=PLATFORM_NAMES[key], variable=value).pack(
                side='left', padx=(0, 12)
            )
        self.slots = tk.StringVar(value=str(self.config.get('slots', 1)))
        ttk.Label(choices, text='远程任务容量').pack(side='left', padx=(20, 5))
        ttk.Combobox(
            choices, textvariable=self.slots, values=['1', '2'], width=4, state='readonly'
        ).pack(side='left')
        self.auto_start = tk.BooleanVar(value=self.config.get('auto_start') is True)
        ttk.Checkbutton(
            area, text='登录 Windows 后自动启动远程采集（启动时保存）', variable=self.auto_start
        ).pack(anchor='w', pady=10)
        buttons = ttk.Frame(area)
        buttons.pack(fill='x', pady=12)
        ttk.Button(
            buttons, text='保存并启动远程采集', command=self.start_worker, style='Primary.TButton'
        ).pack(side='left', padx=(0, 12))
        ttk.Button(buttons, text='停止领取任务', command=self.stop_worker).pack(side='left')
        ttk.Label(
            area,
            text='本机批次与远程采集共用设备锁，请勿同时执行。Cookie 只保存在本机。',
            style='Muted.TLabel',
        ).pack(anchor='w', pady=12)

    def import_config(self):
        if self.busy():
            messagebox.showinfo('设备忙', '请先等待采集或登录结束。', parent=self.window)
            return
        path = filedialog.askopenfilename(
            parent=self.window, title='选择接入配置', filetypes=[('JSON 配置', '*.json')]
        )
        if not path:
            return
        try:
            imported = self.controller.import_connection(path)
            for key, value in self.connection_values.items():
                value.set(imported[key])
            self.auto_start.set(False)
            self.controller.set_startup(False)
            self.status.set('配置已导入，登录平台后保持窗口打开即可启动远程采集。')
        except Exception as exc:
            messagebox.showerror('导入失败', str(exc), parent=self.window)

    def start_worker(self):
        if self.busy():
            messagebox.showinfo('设备忙', '请先等待当前任务或浏览器启动结束。', parent=self.window)
            return
        try:
            self.controller.save_configuration(
                {
                    **{key: value.get().strip() for key, value in self.connection_values.items()},
                    'platforms': [
                        key for key, value in self.worker_platforms.items() if value.get()
                    ],
                    'slots': int(self.slots.get()),
                    'auto_start': self.auto_start.get(),
                }
            )
            self.controller.clear_stop()
            self.spawn('worker', '--run')
            self.status.set('正在连接服务器…')
        except Exception as exc:
            messagebox.showerror('启动失败', str(exc), parent=self.window)

    def stop_worker(self):
        self.controller.stop()
        self.status.set('已停止领取新任务，等待当前任务完成。')

    def poll(self):
        worker = self.processes['worker']
        if worker and worker.poll() is None:
            try:
                state = self.controller.worker_status()
                if state.get('run_id') == self.active_run:
                    self.status.set(state['message'] + ' · 进行中：' + str(state['running']))
            except (OSError, ValueError):
                pass
        elif worker:
            self.status.set(
                '远程采集已结束。'
                if worker.returncode == 0
                else '远程采集异常退出，请查看 client.log。'
            )
            self.processes['worker'] = None
        login = self.processes['login']
        if login and login.poll() is not None:
            self.status.set(
                '浏览器操作已完成，专用窗口可常驻；账号状态在采集时检查。'
                if login.returncode == 0
                else '浏览器操作失败，请查看 client.log 和平台日志。'
            )
            self.processes['login'] = None
        self.browser_launchers = [p for p in self.browser_launchers if p.poll() is None]
        if self.current == 'platforms':
            for key, status in self.controller.browser_sessions().items():
                if self.platform_table.exists(key):
                    self.platform_table.set(key, 'session', status)
        self.poll_job = self.window.after(1000, self.poll)

    def on_destroy(self, event):
        if event.widget is self.window:
            self.window.after_cancel(self.poll_job)

    def close(self):
        if self.busy():
            if self.processes['worker'] and self.processes['worker'].poll() is None:
                self.stop_worker()
            messagebox.showinfo(
                '任务进行中',
                '请停止后续任务，等待采集或浏览器准备完成后再退出。',
                parent=self.window,
            )
            return
        self.window.destroy()
