"""Compact single-window workspace and account UI; download logic stays in the queue."""
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk

import account_login as account
import video_downloader as core
import batch_download as batch


class WorkspaceTabs(tk.Frame):
    """Native focusable buttons with a short, interruptible selection indicator."""
    def __init__(self, parent, notebook, pages):
        super().__init__(parent, background='#f0ebf8')
        self.notebook = notebook
        self.pages = [str(page) for page, _ in pages]
        self.buttons = []
        self.active = -1
        self.timer = None
        self.position = None
        for index, (_, title) in enumerate(pages):
            self.columnconfigure(index, weight=1, uniform='tabs')
            button = tk.Button(self, text=title, command=lambda i=index: self.choose(i),
                font=('Microsoft YaHei UI', 10), borderwidth=0, relief='flat',
                background='#f0ebf8', foreground='#756c85', activebackground='#e9e1f4',
                activeforeground='#5b3c8c', highlightthickness=1, highlightbackground='#f0ebf8',
                highlightcolor='#9676c9', padx=12, pady=6, cursor='hand2', takefocus=True)
            button.grid(row=0, column=index, sticky='ew', padx=2)
            button.bind('<Enter>', lambda event, i=index: self.hover(i, True))
            button.bind('<Leave>', lambda event, i=index: self.hover(i, False))
            for key, delta in (('<Left>', -1), ('<Right>', 1)):
                button.bind(key, lambda event, i=index, d=delta: self.choose((i+d) % len(self.pages), True))
            button.bind('<Home>', lambda event: self.choose(0, True))
            button.bind('<End>', lambda event: self.choose(len(self.pages)-1, True))
            button.bind('<Return>', lambda event, i=index: self.choose(i))
            self.buttons.append(button)
        self.line = tk.Canvas(self, height=3, background='#f0ebf8', highlightthickness=0)
        self.line.grid(row=1, column=0, columnspan=len(pages), sticky='ew')
        self.indicator = self.line.create_rectangle(0, 0, 0, 3, fill='#7955b5', outline='')
        self.bind('<Configure>', lambda event: self.sync(animate=False))
        self.bind('<Destroy>', self.dispose)

    def choose(self, index, focus=False):
        self.notebook.select(self.pages[index])
        if focus:
            self.buttons[index].focus_set()
        self.sync()
        return 'break'

    def hover(self, index, entered):
        selected = index == self.active
        self.buttons[index].configure(background='#e6dcf5' if selected else ('#eae3f3' if entered else '#f0ebf8'))

    def dispose(self, event):
        if event.widget is self and self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None

    def sync(self, animate=True):
        selected = self.notebook.select()
        if selected not in self.pages:
            return
        index = self.pages.index(selected)
        changed = index != self.active
        self.active = index
        for i, button in enumerate(self.buttons):
            button.configure(foreground='#53327f' if i == index else '#756c85',
                font=('Microsoft YaHei UI', 10, 'bold' if i == index else 'normal'),
                highlightbackground='#e6dcf5' if i == index else '#f0ebf8')
            self.hover(i, False)
        if not changed and animate:
            return
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
        width = max(self.line.winfo_width(), self.winfo_width()) / len(self.pages)
        target = (index * width + 14, (index+1) * width - 14)
        start = self.position or target
        began = time.monotonic()
        def frame():
            self.timer = None
            fraction = min(1.0, (time.monotonic()-began)/0.16) if animate else 1.0
            eased = 1 - (1-fraction)**3
            self.position = tuple(a+(b-a)*eased for a, b in zip(start, target))
            self.line.coords(self.indicator, self.position[0], 0, self.position[1], 3)
            if fraction < 1:
                self.timer = self.after(16, frame)
        frame()


class WorkspaceView:
    def build_workspace(self):
        config = core.read_settings()
        self.batch_locked = False
        self.current_key = None
        self.preview_keys = ()
        self.auto_check = tk.BooleanVar(value=config.get('auto_check', True))
        self.body = ttk.Frame(self.root, padding=12)
        self.body.pack(fill='both', expand=True, padx=8, pady=8)
        self.body.rowconfigure(2, weight=1)
        self.body.columnconfigure(0, weight=1)
        header = ttk.Frame(self.body)
        header.grid(row=0, column=0, sticky='ew', pady=(0, 8))
        ttk.Label(header, text='视频下载工具', style='Title.TLabel').pack(side='left')
        self.cookie_label = tk.StringVar(value='已记住登录来源 · 待验证' if self.parent.cookie_file else '未登录 · 使用公开画质')
        ttk.Button(header, text='账号 / 登录', command=self.show_account).pack(side='right')
        ttk.Label(header, textvariable=self.cookie_label).pack(side='right', padx=12)
        style = ttk.Style(self.root)
        style.layout('Workspace.TNotebook.Tab', [])
        self.pages = ttk.Notebook(self.body, style='Workspace.TNotebook')
        self.pages.grid(row=2, column=0, sticky='nsew', pady=(6, 0))
        self.download_page = ttk.Frame(self.pages, padding=10)
        self.account_page = ttk.Frame(self.pages, padding=20)
        self.settings_page = ttk.Frame(self.pages, padding=16)
        self.history_frame = ttk.Frame(self.pages, padding=12)
        tab_pages = [(self.download_page, '下载'), (self.account_page, '账号'),
                     (self.history_frame, '历史任务'), (self.settings_page, '设置与空间')]
        for page, title in tab_pages:
            self.pages.add(page, text=title)
        self.tabbar = WorkspaceTabs(self.body, self.pages, tab_pages)
        self.tabbar.grid(row=1, column=0, sticky='ew')
        self.pages.bind('<<NotebookTabChanged>>', self.page_changed)
        self.build_download_page()
        self.build_account_page()
        self.build_preferences(config)
        self.build_history_page()

    def build_download_page(self):
        page = self.download_page
        page.columnconfigure(0, weight=1)
        page.rowconfigure(3, weight=1)
        source = ttk.Frame(page)
        source.grid(row=0, column=0, sticky='ew')
        source.columnconfigure(0, weight=1)
        self.input = tk.Text(source, height=2, wrap='word', font=('Microsoft YaHei UI', 10), relief='solid', borderwidth=1)
        self.input.grid(row=0, column=0, sticky='ew', padx=(0, 8))
        self.input.insert('1.0', self.parent.url.get())
        self.parse_button = ttk.Button(source, text='识别并检查画质', command=self.parse, style='Primary.TButton')
        self.parse_button.grid(row=0, column=1)
        options = ttk.Frame(page)
        options.grid(row=1, column=0, sticky='ew', pady=6)
        ttk.Label(options, text='普通视频范围：').pack(side='left')
        self.expand = tk.BooleanVar(value=False)
        self.scope_text = tk.StringVar(value='仅当前视频 / 分 P')
        self.scope_box = ttk.Combobox(options, textvariable=self.scope_text, state='readonly', width=23,
                                     values=('仅当前视频 / 分 P', '展开所在合集'))
        self.scope_box.pack(side='left')
        self.scope_box.bind('<<ComboboxSelected>>', self.scope_changed)
        self.import_button = ttk.Button(options, text='导入链接 TXT', command=self.import_links)
        self.import_button.pack(side='left', padx=8)
        self.download_mode = tk.StringVar(value='视频（含音频）')
        self.mode_box = ttk.Combobox(options, textvariable=self.download_mode, state='readonly', width=21,
            values=('视频（含音频）', '仅音频（原始音质）'))
        self.mode_box.pack(side='right')
        self.mode_box.bind('<<ComboboxSelected>>', self.mode_changed)
        ttk.Label(options, text='下载内容：').pack(side='right')
        self.status = tk.StringVar(value='粘贴链接，先确认清单和画质，再开始下载。')
        self.status_label = ttk.Label(page, textvariable=self.status, wraplength=900)
        self.status_label.grid(row=2, column=0, sticky='ew', pady=(0, 6))
        frame = ttk.Frame(page)
        frame.grid(row=3, column=0, sticky='nsew')
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.table = ttk.Treeview(frame, columns=('number', 'title', 'resolution', 'fps', 'status'),
                                 show='headings', selectmode='extended', height=6)
        for key, title, width in [('number', '#', 40), ('title', '视频标题', 360),
                                  ('resolution', '可下载分辨率', 140), ('fps', '帧率', 60), ('status', '状态', 150)]:
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=35, stretch=key == 'title')
        self.table.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.table.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.table.configure(yscrollcommand=scroll.set)
        self.table.bind('<<TreeviewSelect>>', self.selection_changed)
        actions = ttk.Frame(page)
        actions.grid(row=4, column=0, sticky='ew', pady=6)
        self.all_button = ttk.Button(actions, text='全选', command=lambda: self.table.selection_set(self.table.get_children()))
        self.all_button.pack(side='left')
        self.none_button = ttk.Button(actions, text='清空选择', command=lambda: self.table.selection_remove(self.table.selection()))
        self.none_button.pack(side='left', padx=4)
        selection = ttk.Menubutton(actions, text='按条件选择 ▾')
        menu = tk.Menu(selection, tearoff=False)
        for title, command in [('未完成', self.select_unfinished), ('失败', self.select_failed), ('已检查的 4K', self.select_4k),
                               ('标题含原版', lambda: self.select_version('原版')), ('标题含中文', lambda: self.select_version('中文'))]:
            menu.add_command(label=title, command=command)
        selection.configure(menu=menu)
        selection.pack(side='left', padx=4)
        self.extra_buttons = [selection]
        self.selection_text = tk.StringVar(value='已选 0 条')
        ttk.Label(actions, textvariable=self.selection_text).pack(side='left', padx=4)
        self.download_button = ttk.Button(actions, text='预览并下载', style='Primary.TButton', command=self.start)
        self.download_button.pack(side='right')
        self.quality_button = ttk.Button(actions, text='补查画质', command=self.check_qualities)
        self.quality_button.pack(side='right', padx=6)
        self.confirmation = ttk.Frame(page, padding=8, relief='solid', borderwidth=1)
        self.confirmation.columnconfigure(0, weight=1)
        self.confirmation.rowconfigure(0, weight=1)
        self.preview_text = tk.StringVar()
        self.preview_label = tk.Text(self.confirmation, height=5, wrap='word', font=('Microsoft YaHei UI', 10), state='disabled', borderwidth=0)
        self.preview_label.grid(row=0, column=0, sticky='nsew')
        preview_scroll = ttk.Scrollbar(self.confirmation, orient='vertical', command=self.preview_label.yview)
        preview_scroll.grid(row=0, column=1, sticky='ns')
        self.preview_label.configure(yscrollcommand=preview_scroll.set)
        def update_preview(*args):
            self.preview_label.configure(state='normal')
            self.preview_label.delete('1.0', 'end')
            self.preview_label.insert('1.0', self.preview_text.get())
            self.preview_label.configure(state='disabled')
        self.preview_text.trace_add('write', update_preview)
        confirm_actions = ttk.Frame(self.confirmation)
        confirm_actions.grid(row=1, column=0, sticky='e', pady=(5, 0))
        ttk.Button(confirm_actions, text='返回选择', command=self.hide_preview).pack(side='left', padx=5)
        self.confirm_button = ttk.Button(confirm_actions, text='确认下载', style='Primary.TButton', command=self.commit_preview)
        self.confirm_button.pack(side='left')
        # The preview temporarily replaces the list, retaining room for progress on small screens.
        self.table_frame = frame
        selected = ttk.Frame(page)
        selected.grid(row=5, column=0, sticky='ew')
        self.fix_button = ttk.Button(selected, text='处理选中失败', command=self.fix_selected)
        self.fix_button.pack(side='left')
        ttk.Button(selected, text='播放', command=self.open_selected).pack(side='left', padx=4)
        ttk.Button(selected, text='定位文件', command=lambda: self.open_selected(locate=True)).pack(side='left')
        ttk.Button(selected, text='打开下载目录', command=self.open_folder).pack(side='right')
        self.error_text = tk.StringVar(value='')
        self.error_label = ttk.Label(page, textvariable=self.error_text, foreground='#9c3527', wraplength=900)
        self.error_label.grid(row=6, column=0, sticky='ew', pady=3)
        progress = ttk.Frame(page)
        progress.grid(row=7, column=0, sticky='ew', pady=(4, 0))
        progress.columnconfigure(0, weight=1)
        self.current_title = tk.StringVar(value='当前视频：尚未开始')
        ttk.Label(progress, textvariable=self.current_title).grid(row=0, column=0, sticky='w')
        self.stop_button = ttk.Button(progress, text='暂停', command=self.cancel, state='disabled')
        self.stop_button.grid(row=0, column=1, padx=5)
        self.now_button = ttk.Button(progress, text='立即重试', command=self.retry_now.set, state='disabled')
        self.now_button.grid(row=0, column=2)
        self.stage_text = tk.StringVar(value='等待开始')
        ttk.Label(progress, textvariable=self.stage_text).grid(row=1, column=0, sticky='w')
        self.bar = ttk.Progressbar(progress, maximum=100)
        self.bar.grid(row=2, column=0, columnspan=3, sticky='ew', pady=4)
        self.file_progress = tk.StringVar(value='百分比、大小、速度与剩余时间会显示在这里。')
        self.progress_label = ttk.Label(progress, textvariable=self.file_progress, wraplength=900)
        self.progress_label.grid(row=3, column=0, columnspan=3, sticky='ew')
        self.total_text = tk.StringVar(value='尚未开始批次')
        ttk.Label(progress, textvariable=self.total_text).grid(row=4, column=0, columnspan=3, sticky='w', pady=3)
        self.total_bar = ttk.Progressbar(progress, maximum=100)
        self.total_bar.grid(row=5, column=0, columnspan=3, sticky='ew')
        self.retry_text = tk.StringVar(value='可恢复故障会在本轮结束后自动重试。')
        ttk.Label(progress, textvariable=self.retry_text).grid(row=6, column=0, columnspan=3, sticky='w')
        self.detail = tk.StringVar(value='')
        # Legacy queue controls remain addressable, but are collected in Settings.
        self.compat_controls = ttk.Frame(self.settings_page)
        self.retry_button = ttk.Button(self.compat_controls, command=self.retry)
        self.only4k_button = ttk.Button(self.compat_controls, command=self.select_4k)
        self.expand_button = ttk.Checkbutton(self.compat_controls, variable=self.expand)
        self.root.bind('<Configure>', self.resize_labels, add='+')

    def build_account_page(self):
        page = self.account_page
        ttk.Label(page, text='在这里登录 B 站', style='Title.TLabel').pack(anchor='w')
        ttk.Label(page, text='用手机 B 站 App 扫码，并在手机上确认登录。不需要浏览器插件或 Cookie 文件。', wraplength=760).pack(anchor='w', pady=10)
        self.login_text = tk.StringVar(value='点击“生成登录二维码”开始。')
        self.login_heading = tk.StringVar(value='尚未登录')
        self.login_state = 'idle'
        self.login_card = tk.Frame(page, background='#eee8f8', padx=16, pady=10)
        self.login_card.pack(fill='x')
        self.login_heading_label = tk.Label(self.login_card, textvariable=self.login_heading,
            font=('Microsoft YaHei UI', 20, 'bold'), anchor='w', background='#eee8f8', foreground='#493077')
        self.login_heading_label.pack(fill='x')
        self.login_detail_label = tk.Label(self.login_card, textvariable=self.login_text,
            font=('Microsoft YaHei UI', 10), anchor='w', justify='left', wraplength=680,
            background='#eee8f8', foreground='#493077')
        self.login_detail_label.pack(fill='x', pady=(3, 0))
        self.return_download_button = ttk.Button(self.login_card, text='返回下载 · 检查画质',
            command=lambda: self.pages.select(self.download_page), style='Primary.TButton')
        self.qr_label = ttk.Label(page, text='登录二维码将在这里显示', anchor='center')
        self.qr_label.pack(pady=12)
        self.remember_login = tk.BooleanVar(value=True)
        self.remember_check = ttk.Checkbutton(page, text='记住登录（加密保存在本机，仅当前 Windows 用户可用）', variable=self.remember_login)
        self.remember_check.pack(anchor='w')
        row = ttk.Frame(page)
        row.pack(anchor='w', pady=12)
        self.login_button = ttk.Button(row, text='生成登录二维码', command=self.begin_login, style='Primary.TButton')
        self.login_button.pack(side='left')
        self.login_cancel = ttk.Button(row, text='取消扫码', command=self.cancel, state='disabled')
        self.login_cancel.pack(side='left', padx=6)
        self.verify_button = ttk.Button(row, text='验证登录状态', command=self.verify_login)
        self.verify_button.pack(side='left', padx=6)
        self.logout_button = ttk.Button(row, text='退出登录', command=self.logout)
        self.logout_button.pack(side='left')
        ttk.Separator(page).pack(fill='x', pady=8)
        ttk.Label(page, text='兼容方式：已有 Cookie 文件时可以导入。扫码服务需要额外验证时，可先在 B 站 App 处理。', wraplength=760).pack(anchor='w')
        self.cookie_button = ttk.Button(page, text='导入 Cookie 文件', command=self.choose_cookie)
        self.cookie_button.pack(anchor='w', pady=8)
        if self.parent.cookie_file:
            self.show_login_state('unverified', '登录来源待验证', '已找到登录来源；点击“验证登录状态”确认是否有效。')

    def show_login_state(self, state, heading, detail):
        self.login_state = state
        self.login_heading.set(heading)
        self.login_text.set(detail)
        background, foreground = {
            'success': ('#e0f3e8', '#17623a'),
            'error': ('#fbe5e5', '#9b2929'),
            'expired': ('#fff0d2', '#865710'),
            'confirm': ('#fff0d2', '#865710'),
        }.get(state, ('#eee8f8', '#493077'))
        self.login_card.configure(background=background)
        for label in (self.login_heading_label, self.login_detail_label):
            label.configure(background=background, foreground=foreground)
        self.return_download_button.pack_forget()
        if state == 'success':
            self.return_download_button.pack(anchor='w', pady=(10, 0))
        if state not in ('waiting', 'confirm'):
            self.qr_label.configure(image='', text='')
            self.qr_label.image = None

    def build_preferences(self, config):
        page = self.settings_page
        ttk.Label(page, text='下载偏好与存储', style='Title.TLabel').pack(anchor='w', pady=(0, 10))
        self.output_text = tk.StringVar(value=str(self.parent.output_dir))
        ttk.Label(page, textvariable=self.output_text, wraplength=760).pack(anchor='w')
        row = ttk.Frame(page)
        row.pack(fill='x', pady=8)
        self.output_button = ttk.Button(row, text='更换保存目录', command=self.choose_output)
        self.output_button.pack(side='left')
        self.disk_text = tk.StringVar()
        ttk.Label(row, textvariable=self.disk_text).pack(side='left', padx=10)
        self.cache_button = ttk.Button(row, text='检查可安全清理缓存', command=self.scan_cache)
        self.cache_button.pack(side='left')
        ttk.Label(page, text='限速（下载中可实时调整）').pack(anchor='w', pady=(12, 4))
        row = ttk.Frame(page)
        row.pack(fill='x')
        self.rate_enabled = tk.BooleanVar(value=config.get('rate_limit_enabled', True))
        self.rate_mb = tk.StringVar(value=str(config.get('rate_limit_mb', '2')))
        ttk.Checkbutton(row, text='启用限速', variable=self.rate_enabled).pack(side='left')
        ttk.Combobox(row, textvariable=self.rate_mb, values=('0.5', '1', '2', '5', '10'), width=6).pack(side='left')
        ttk.Label(row, text='MB/s').pack(side='left', padx=4)
        ttk.Button(row, text='立即应用', command=self.apply_rate).pack(side='left')
        self.rate_text = tk.StringVar(value='默认 2 MB/s，可按家庭网络情况调整')
        ttk.Label(page, textvariable=self.rate_text).pack(anchor='w', pady=5)
        ttk.Checkbutton(page, text='解析清单后自动补查选中视频画质（不自动下载）', variable=self.auto_check,
                        command=self.save_preferences).pack(anchor='w', pady=10)
        row = ttk.Frame(page)
        row.pack(fill='x', pady=5)
        ttk.Label(row, text='最多读取').pack(side='left')
        self.limit = tk.StringVar(value='500')
        self.limit_box = ttk.Spinbox(row, from_=1, to=2000, textvariable=self.limit, width=7)
        self.limit_box.pack(side='left', padx=5)
        ttk.Label(row, text='条；超出时预览会提醒').pack(side='left')
        self.refresh_quality_button = ttk.Button(page, text='强制重查选中画质', command=lambda: self.check_qualities(force=True))
        self.refresh_quality_button.pack(anchor='w', pady=8)
        row = ttk.Frame(page)
        row.pack(fill='x', pady=8)
        ttk.Label(row, text='背景浓度').pack(side='left')
        self.background_level = tk.DoubleVar(value=float(config.get('background_opacity', 0.2)) * 100)
        ttk.Scale(row, from_=0, to=100, variable=self.background_level, command=self.adjust_background, length=180).pack(side='left', padx=6)
        self.background_text = tk.StringVar(value=f'{self.background_level.get():.0f}%')
        ttk.Label(row, textvariable=self.background_text).pack(side='left')
        ttk.Label(page, text='处理详情（不包含登录凭据）').pack(anchor='w', pady=(12, 4))
        ttk.Label(page, textvariable=self.detail, wraplength=760).pack(anchor='w')

    def build_history_page(self):
        self.history_frame.columnconfigure(0, weight=1)
        self.history_frame.rowconfigure(0, weight=1)
        self.history = ttk.Treeview(self.history_frame, columns=('title','counts','output','updated'), show='headings')
        for key,title,width in [('title','历史任务',240),('counts','完成 / 总数',95),('output','保存位置',280),('updated','更新时间',150)]:
            self.history.heading(key,text=title)
            self.history.column(key,width=width,stretch=key in ('title','output'))
        self.history.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(self.history_frame, orient='vertical', command=self.history.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.history.configure(yscrollcommand=scroll.set)
        self.history.bind('<Double-1>', lambda e: self.load_history())
        row = ttk.Frame(self.history_frame)
        row.grid(row=1, column=0, sticky='e', pady=8)
        self.restore_button = ttk.Button(row, text='恢复上次任务', command=lambda: self.restore_task(force=True))
        self.restore_button.pack(side='left', padx=6)
        self.history_restore_button = ttk.Button(row, text='恢复选中历史任务', command=self.load_history)
        self.history_restore_button.pack(side='left')

    def resize_labels(self, event=None):
        if event is not None and event.widget != self.root:
            return
        width = max(440, self.root.winfo_width() - 90)
        for label in (self.status_label, self.error_label, self.progress_label):
            label.configure(wraplength=width)

    def page_changed(self, event=None):
        self.tabbar.sync()
        if hasattr(self, 'history') and self.pages.select() == str(self.history_frame):
            self.refresh_history()

    def show_account(self):
        self.pages.select(self.account_page)

    def scope_changed(self, event=None):
        self.expand.set(self.scope_text.get() == '展开所在合集')
        self.hide_preview()
        self.input_modified()
        if self.collection:
            self.status.set('下载范围已改变，请重新识别清单。')
            self.download_button.configure(state='disabled')
            self.quality_button.configure(state='disabled')

    def save_preferences(self):
        import json
        settings = core.read_settings()
        settings['auto_check'] = self.auto_check.get()
        path = core.APP_DIR / 'settings.json'
        try:
            temp = path.with_suffix('.json.tmp')
            temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(path)
        except OSError:
            self.status.set('设置暂时无法保存，当前窗口仍可使用。')

    def show_preview(self):
        keys = tuple(self.table.selection())
        if not keys:
            self.status.set('先选择要下载的视频。')
            return
        self.preview_keys = keys
        known = [self.qualities[k] for k in keys if batch.valid_quality(self.qualities.get(k),
                 self.collection['entries'][int(k)]['url'], self.use_chrome, self.cookie_file)]
        estimated = sum(q.get('estimated_bytes') or 0 for q in known)
        unknown_size = len(keys) - sum(bool(q.get('estimated_bytes')) and not q.get('size_partial', True) for q in known)
        qualities = sorted({core.quality_text(q) for q in known})
        existing = sum(bool(self.download_paths.get(k)) and Path(self.download_paths[k]).is_file() for k in keys)
        unknown = len(keys) - len(known)
        self.preview_text.set(
            f"下载模式：{self.download_mode.get()}\n确认内容：{self.collection['title']}\n来源：{self.collection['reference']}\n"
            f"选中 {len(keys)} / {len(self.collection['entries'])} 条 · {'仅当前视频' if self.collection.get('single_video') else '合集 / 剧集 / 多链接清单'}\n"
            + ('清单达到读取上限，只包含部分内容。\n' if self.collection.get('truncated') else '')
            + '已确认画质：' + ('；'.join(qualities[:4]) or '暂无')
            + (f'（共 {len(qualities)} 种）' if len(qualities) > 4 else '') + '\n'
            + f'画质未确认：{unknown} 条；确认下载后尝试最高可用画质。\n'
            + f'已知传输量约 {core.format_size(estimated)}；大小未完整识别 {unknown_size} 条（合并另需空间）。\n'
            + f'历史成品可找到 {existing} 条；下载时仍核验同内容同画质，符合才跳过。\n'
            + f'保存到：{self.folder}')
        if self.audio_mode():
            self.preview_text.set(self.preview_text.get().replace('画质','音轨').replace('（合并另需空间）','（保留原始音质，不下载视频流）'))
        self.confirm_button.configure(text=f'确认下载 {len(keys)} 条' + ('（含未识别）' if unknown else ''))
        self.table_frame.grid_remove()
        self.confirmation.grid(row=3, column=0, sticky='nsew')

    def hide_preview(self):
        self.preview_keys = ()
        self.confirmation.grid_remove()
        self.table_frame.grid(row=3, column=0, sticky='nsew')

    def commit_preview(self):
        if self.running or not self.list_is_current():
            return
        if self.preview_keys != tuple(self.table.selection()):
            self.hide_preview()
            self.status.set('选择已变化，请重新预览。')
            return
        self.hide_preview()
        self._start_confirmed()

    def begin_login(self):
        if self.running:
            return
        try:
            import qrcode  # noqa: F401
        except ImportError:
            self.show_login_state('error', '无法开始登录', '二维码组件缺失，请重新运行安装程序修复。')
            return
        self.login_remember_requested = self.remember_login.get()
        self.operation = 'login'
        self.show_login_state('loading', '正在生成二维码…', '正在连接 B 站，请稍候。')
        self.qr_label.configure(image='', text='正在连接 B 站…')
        self.worker(self.login_flow, 'account_done')

    def login_flow(self):
        client = account.LoginClient()
        ticket = client.generate()
        if self.stop.is_set():
            return {'cancelled': True}
        self.emit('account_qr', ticket['url'])
        deadline = time.monotonic() + 180
        while not self.stop.wait(3):
            if time.monotonic() >= deadline:
                return {'expired': True}
            status, profile = client.poll(ticket['key'])
            if self.stop.is_set():
                return {'cancelled': True}
            if status == 'success':
                return {'jar': client.jar, 'profile': profile}
            if status == 'expired':
                return {'expired': True}
            self.emit('account_wait', '已扫码，请在手机上确认登录。' if status == 'confirm' else '等待手机 B 站 App 扫码…')
        return {'cancelled': True}

    def verify_login(self):
        if self.running:
            return
        if not self.cookie_file:
            self.cookie_label.set('未登录 · 使用公开画质')
            self.show_login_state('idle', '尚未登录', '还没有登录，请生成二维码。')
            return
        cookie = self.cookie_file
        self.operation = 'verify_login'
        self.show_login_state('loading', '正在验证账号…', '正在向 B 站确认登录是否有效，请稍候。')
        self.worker(lambda: account.LoginClient(core.load_cookie_file(cookie, 'https://www.bilibili.com/')).verify(), 'account_verified')

    def account_changed(self, source, persist=True):
        core.remember_cookie_path(source if persist else None)
        self.cookie_file = self.parent.cookie_file = source
        self.qualities.clear()
        for key in self.table.get_children():
            self.table.set(key, 'resolution', '需重新检查')
        self.hide_preview()
        self.status.set('登录来源已更新，请重新检查选中画质后确认下载。')

    def logout(self):
        if self.running:
            return
        try:
            self.account_changed(None)
            account.STORE.logout()
        except (OSError, account.LoginError):
            self.show_login_state('error', '登录资料清理失败', '请检查本机目录权限后再次点击“退出登录”。')
            return
        self.cookie_label.set('未登录 · 使用公开画质')
        self.show_login_state('idle', '已退出登录', '已清除本工具保存的登录态，可重新扫码。')
        self.qr_label.configure(image='', text='登录二维码将在这里显示')
        self.qr_label.image = None

    def workspace_event(self, kind, value):
        if kind == 'account_qr':
            if self.stop.is_set():
                return
            import qrcode
            qr = qrcode.QRCode(border=4, box_size=4)
            qr.add_data(value)
            qr.make(fit=True)
            matrix = qr.get_matrix()
            scale = max(2, 200 // len(matrix))
            image = tk.PhotoImage(width=len(matrix)*scale, height=len(matrix)*scale)
            for y, row in enumerate(matrix):
                colors = ' '.join('black' if bit else 'white' for bit in row for _ in range(scale))
                for dy in range(scale):
                    image.put('{' + colors + '}', to=(0, y*scale+dy))
            self.qr_label.configure(image=image, text='')
            self.qr_label.image = image
            self.show_login_state('waiting', '第 1 步：用 B 站 App 扫码', '扫码后还需在手机上确认；出现绿色“登录成功”才表示已接入。')
        elif kind == 'account_wait':
            confirmed = '已扫码' in value
            self.show_login_state('confirm' if confirmed else 'waiting',
                '第 2 步：请在手机上确认登录' if confirmed else '第 1 步：等待手机扫码',
                '已识别扫码，请在手机上点击确认，完成后这里会自动显示登录结果。' if confirmed else value)
        elif kind == 'account_done':
            self.set_running(False)
            self.operation = ''
            self.qr_label.configure(image='', text='')
            self.qr_label.image = None
            if self.stop.is_set() or value.get('cancelled'):
                self.show_login_state('idle', '已取消扫码', '本次扫码已停止，可以重新生成二维码。')
            elif value.get('expired'):
                self.show_login_state('expired', '二维码已过期', '本次未完成登录，请点击“生成登录二维码”重试。')
            else:
                try:
                    account.STORE.accept(value['jar'], remember=self.login_remember_requested)
                    self.account_changed(account.SESSION_REF, persist=self.login_remember_requested)
                    self.set_account_profile(value['profile'])
                except (OSError, account.LoginError):
                    self.show_login_state('error', '登录未完成', '登录资料未能保存，请取消“记住登录”后重试，或检查本机目录权限。')
        elif kind == 'account_verified':
            self.set_running(False)
            self.operation = ''
            self.set_account_profile(value)
        elif kind == 'error' and self.operation in ('login', 'verify_login'):
            self.qr_label.configure(image='', text='')
            self.qr_label.image = None
            self.show_login_state('error', '登录尚未验证成功', str(value) + '\n请检查网络后验证登录，或重新生成二维码。')
            self.cookie_label.set('登录尚未验证 · 可重新登录')
        elif kind == 'stage':
            self.stage_text.set(value)
            if value.startswith(('合并', '校验')):
                self.bar.configure(mode='indeterminate')
                self.bar.start(14)
                self.file_progress.set('音视频传输已结束，正在处理本地文件；剩余时间暂不可估算。')
        elif kind == 'progress':
            if value.get('percent') is None:
                if self.bar.cget('mode') != 'indeterminate':
                    self.bar.configure(mode='indeterminate')
                    self.bar.start(14)
            else:
                self.bar.stop()
                self.bar.configure(mode='determinate')
        elif kind == 'item':
            if value.get('error') and batch.failure_policy(value['error'])['action'] == 'login':
                self.cookie_label.set('登录已失效 · 请重新登录')
                self.show_login_state('expired', '登录已失效', '请重新扫码登录，然后补查画质并继续未完成任务。')
            if value.get('state') == '正在下载':
                self.current_key = value['key']
                self.current_title.set('当前：' + self.collection['entries'][int(value['key'])]['title'][:90])
                self.stage_text.set('检查本地文件与可用画质')
                self.bar.stop()
                self.bar.configure(mode='determinate')
            elif value.get('state') in ('已完成', '已存在', '失败', '待续传'):
                self.bar.stop()
                self.bar.configure(mode='determinate')
                self.stage_text.set(value['state'])
                if value['state'] in ('已完成', '已存在'):
                    self.bar['value'] = 100
                    self.file_progress.set('成品已保存，可播放或定位文件。' if value['state'] == '已完成' else '同内容同画质成品已核验，跳过重复下载。')
        elif kind in ('finished', 'error', 'quality_finished'):
            self.bar.stop()
            self.bar.configure(mode='determinate')
            if kind == 'finished':
                self.stage_text.set('已暂停 · 片段保留，可继续' if value.get('stopped') else '批次结束，请查看完成与待处理数量')

    def set_account_profile(self, profile):
        if profile['logged_in']:
            if self.cookie_file == account.SESSION_REF:
                account.STORE.verified_at = time.monotonic()
            self.cookie_label.set(f"已登录：{profile['name']} · {profile['checked_at']} 验证")
            persistence = '已加密记住登录，下次打开可再次验证。' if account.STORE.path.is_file() and self.cookie_file == account.SESSION_REF else '当前登录已生效；未记住登录时，关闭工具后需要重新扫码。'
            self.show_login_state('success', '✓ 登录成功',
                f"账号：{profile['name']}   ·   {profile['checked_at']} 已通过 B 站验证\n"
                f'已接入下载器，可以返回下载页检查画质。{persistence}')
            self.status.set(f"已登录：{profile['name']}。可补查画质，再确认下载。")
        else:
            self.cookie_label.set('登录已失效 · 请重新登录')
            self.show_login_state('expired', '登录已失效', 'B 站未确认有效登录，请重新生成二维码并在手机上确认。')
            self.qualities.clear()
            for key in self.table.get_children():
                self.table.set(key, 'resolution', '需重新检查')
            self.hide_preview()
