"""One-window download workspace, built on the existing tested download queue."""
import functools
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from datetime import datetime
import tkinter as tk
from tkinter import ttk
import webbrowser

import video_downloader as core
import batch_download as batch
import cache_manager


def canonical_input(value):
    try:return tuple(core.normalize_url(line) for line in value.splitlines() if line.strip())
    except ValueError:return (value.strip(),)


def resolve_input(raw,limit,expand,use_chrome,cookie,stop,emit):
    urls=list(canonical_input(raw))
    if not urls:raise ValueError('请先粘贴链接。')
    if len(urls)>1 or expand or core.bangumi_id(urls[0],'ss') or 'space.bilibili.com/' in urls[0]:
        return batch.enumerate_collection(raw,limit,use_chrome,cookie,stop,emit)
    url=core.normalize_url(urls[0])
    identity=core.video_id_from_url(url) or '单个视频'
    result={'title':identity,'reference':url,'expected_count':1,'truncated':False,'single_video':True,
            'entries':[{'url':url,'id':identity,'title':identity}]}
    try:
        q=core.download_video(url,use_chrome=use_chrome,cookie_file=cookie,stop=stop,emit=emit,preview_only=True)
        result['title']=result['entries'][0]['title']=q['title']
        result['entries'][0]['id']=q['id'];result['_quality']=q
    except core.Cancelled:raise
    except Exception as exc:result['_error']=core.describe_error(exc)
    return result


class UnifiedApp(batch.BatchWindow):
    def __init__(self,root,initial_url='',history_dir=None):
        config=core.read_settings()
        self.history_dir=Path(history_dir) if history_dir else core.APP_DIR/'任务历史'
        self.controller=core.LiveRateController(core.configured_rate_limit())
        self.cache_plan=None;self.operation='';self.history_rows={};self.history_loading=False
        self.pause_requested=False
        style=ttk.Style(root);style.theme_use('clam')
        style.configure('TFrame',background='#f8f5ff')
        style.configure('TLabel',background='#f8f5ff',font=('Microsoft YaHei UI',10))
        style.configure('Title.TLabel',font=('Microsoft YaHei UI',20,'bold'),foreground='#493077')
        style.configure('TButton',padding=(7,5))
        style.configure('Primary.TButton',foreground='#62439f')
        parent=SimpleNamespace(root=root,url=tk.StringVar(value=initial_url),
                               output_dir=Path(config.get('output_dir') or (Path.home()/'Videos'/'BiliDownload')),
                               cookie_file=config.get('cookie_file'),use_chrome=tk.BooleanVar(value=False))
        self.download_handler=functools.partial(core.download_video,rate_controller=self.controller)
        super().__init__(parent,embedded=True)
        self.body.pack_configure(padx=16,pady=(40,14) if self.background else 10)
        self.root.geometry(f'1200x{min(980,self.root.winfo_screenheight()-90)}')
        self.root.minsize(1020,850)
        self.input.bind('<<Modified>>',self.input_modified)
        self.input.edit_modified(False)
        self.table.bind('<Double-1>',lambda e:self.open_selected())
        self.restore_paths()
        self.select_unfinished(initial=True)
        self.import_old_tasks()
        self.refresh_history()
        self.refresh_disk()

    def build_settings(self,body):
        self.body=body
        config=core.read_settings()
        row=ttk.Frame(body);row.pack(fill='x',pady=(6,3))
        self.cookie_label=tk.StringVar(value='Cookie：'+(Path(self.parent.cookie_file).name if self.parent.cookie_file else '未选择'))
        self.cookie_button=ttk.Button(row,text='选择 Cookie',command=self.choose_cookie);self.cookie_button.pack(side='left')
        ttk.Label(row,textvariable=self.cookie_label).pack(side='left',padx=6)
        self.rate_enabled=tk.BooleanVar(value=config.get('rate_limit_enabled',True))
        self.rate_mb=tk.StringVar(value=str(config.get('rate_limit_mb','2')))
        ttk.Checkbutton(row,text='限速',variable=self.rate_enabled).pack(side='left',padx=(12,0))
        ttk.Combobox(row,textvariable=self.rate_mb,values=('0.5','1','2','5','10'),width=6).pack(side='left',padx=4)
        ttk.Label(row,text='MB/s').pack(side='left')
        ttk.Button(row,text='立即应用',command=self.apply_rate).pack(side='left',padx=6)
        self.rate_text=tk.StringVar(value='下载中也可调整');ttk.Label(row,textvariable=self.rate_text).pack(side='left')
        row=ttk.Frame(body);row.pack(fill='x',pady=3)
        self.output_text=tk.StringVar(value=str(self.parent.output_dir))
        ttk.Label(row,text='保存到：').pack(side='left')
        ttk.Label(row,textvariable=self.output_text,width=52).pack(side='left')
        self.output_button=ttk.Button(row,text='更换目录',command=self.choose_output);self.output_button.pack(side='left',padx=4)
        self.disk_text=tk.StringVar();ttk.Label(row,textvariable=self.disk_text).pack(side='left',padx=4)
        self.cache_button=ttk.Button(row,text='检查可清理缓存',command=self.scan_cache);self.cache_button.pack(side='right')
        row=ttk.Frame(body);row.pack(fill='x',pady=3)
        self.expand=tk.BooleanVar(value=False)
        self.expand_button=ttk.Checkbutton(row,text='普通视频链接展开所在合集',variable=self.expand);self.expand_button.pack(side='left')
        ttk.Label(row,text='背景浓度').pack(side='left',padx=(14,4))
        self.background_level=tk.DoubleVar(value=float(config.get('background_opacity',0.2))*100)
        ttk.Scale(row,from_=0,to=100,variable=self.background_level,command=self.adjust_background,length=120).pack(side='left')
        self.background_text=tk.StringVar(value=f"{self.background_level.get():.0f}%")
        ttk.Label(row,textvariable=self.background_text,width=5).pack(side='left',padx=4)
        ttk.Button(row,text='历史任务列表',command=self.toggle_history).pack(side='right')
        self.history_frame=ttk.Frame(body)
        self.history=ttk.Treeview(self.history_frame,columns=('title','counts','output','updated'),show='headings',height=5)
        for key,title,width in [('title','历史任务',260),('counts','完成 / 总数',100),('output','保存位置',350),('updated','更新时间',150)]:
            self.history.heading(key,text=title);self.history.column(key,width=width)
        self.history.pack(fill='x')
        self.history.bind('<Double-1>',lambda e:self.load_history())
        ttk.Button(self.history_frame,text='恢复选中历史任务',command=self.load_history).pack(anchor='e')

    def build_task_actions(self,body):
        row=ttk.Frame(body);row.pack(fill='x',pady=3)
        self.extra_buttons=[]
        for label,command in [('只选未完成',self.select_unfinished),('只选失败',self.select_failed),
                              ('只选原版',lambda:self.select_version('原版')),('只选中文',lambda:self.select_version('中文'))]:
            button=ttk.Button(row,text=label,command=command);button.pack(side='left',padx=(0,4));self.extra_buttons.append(button)
        ttk.Button(row,text='打开选中视频',command=self.open_selected).pack(side='right')
        ttk.Button(row,text='定位文件',command=lambda:self.open_selected(locate=True)).pack(side='right',padx=4)
        self.fix_button=ttk.Button(row,text='处理选中失败',command=self.fix_selected);self.fix_button.pack(side='left',padx=4)

    def set_running(self,running):
        super().set_running(running)
        for button in (self.cookie_button,self.output_button,self.cache_button,self.expand_button,*self.extra_buttons):
            button.configure(state='disabled' if running else 'normal')
        # Limit controls deliberately stay enabled; the active transfer reads the controller.

    def apply_rate(self):
        try:
            limit=core.parse_rate_limit(self.rate_enabled.get(),self.rate_mb.get())
            core.save_rate_limit(self.rate_enabled.get(),self.rate_mb.get())
            self.controller.set_limit(limit)
            self.rate_text.set('已实时应用：'+(f'{limit/1048576:g} MB/s' if limit else '不限速'))
        except (ValueError,OSError) as exc:self.rate_text.set(str(exc))

    def adjust_background(self,value):
        amount=max(0,min(100,float(value)))
        if hasattr(self,'background_text'):
            self.background_text.set(f'{amount:.0f}%')
        if getattr(self,'background',None):
            self.background.set_strength(amount/100)
        if getattr(self,'background_save_timer',None):
            self.root.after_cancel(self.background_save_timer)
        def save():
            self.background_save_timer=None
            try:
                config=core.read_settings();config['background_opacity']=amount/100
                path=core.APP_DIR/'settings.json';temporary=path.with_suffix('.json.tmp')
                temporary.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)
            except OSError:pass
        self.background_save_timer=self.root.after(300,save)

    def choose_cookie(self):
        if self.running:return
        path=self.filedialog.askopenfilename(title='选择本机 Cookie 文件',filetypes=[('Cookie 文件','*.txt')])
        if not path:return
        try:core.remember_cookie_path(path)
        except OSError:self.status.set('Cookie 路径无法保存。');return
        self.cookie_file=self.parent.cookie_file=path
        self.cookie_label.set('Cookie：'+Path(path).name)
        self.qualities.clear()
        for key in self.table.get_children():self.table.set(key,'resolution','需重新检查')
        self.status.set('登录文件已更换，请重新检查画质。')

    def choose_output(self):
        if self.running:return
        chosen=self.filedialog.askdirectory(title='选择保存目录')
        if not chosen:return
        self.parent.output_dir=Path(chosen);self.output_text.set(chosen)
        try:
            config=core.read_settings();config['output_dir']=chosen
            (core.APP_DIR/'settings.json').write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
        except OSError:self.status.set('保存目录可使用，但未能记住设置。')
        if self.collection:
            self.folder=batch.collection_folder(chosen,self.collection['title'],self.collection['reference'])
        self.refresh_disk();self.persist_task()

    def refresh_disk(self):
        if getattr(self,'disk_timer',None):
            self.root.after_cancel(self.disk_timer)
            self.disk_timer=None
        try:
            path=self.parent.output_dir
            while not path.exists():path=path.parent
            free=shutil.disk_usage(path).free
            self.disk_text.set('可用 '+core.format_size(free)+(' · 空间偏少' if free<2*1024**3 else ''))
        except OSError:self.disk_text.set('磁盘空间无法读取')
        if self.root.winfo_exists():self.disk_timer=self.root.after(5000,self.refresh_disk)

    def input_modified(self,event=None):
        if not self.input.edit_modified():return
        self.input.edit_modified(False)
        if self.parsed_raw and canonical_input(self.input.get('1.0','end'))!=canonical_input(self.parsed_raw):
            self.status.set('链接已改变：下面是旧清单，请先重新解析。')
            self.download_button.configure(state='disabled');self.quality_button.configure(state='disabled')
        elif not self.running:
            self.download_button.configure(state='normal');self.quality_button.configure(state='normal')

    def list_is_current(self):
        if not self.collection or canonical_input(self.input.get('1.0','end'))!=canonical_input(self.parsed_raw):
            self.status.set('请先解析当前链接，清单与链接不一致。');return False
        return True

    def parse(self):
        if self.running:return
        try:limit=int(self.limit.get());raw=self.input.get('1.0','end').strip()
        except ValueError:self.status.set('读取上限请输入数字。');return
        if not 1<=limit<=2000:self.status.set('读取上限应为 1～2000。');return
        self.pending_raw=raw;self.collection=None;self.qualities={};self.download_paths={};self.errors={};self.download_states={}
        self.table.delete(*self.table.get_children());self.task_keys=[];self.update_total()
        self.operation='parse';self.status.set('正在识别当前链接和清单…')
        expand=self.expand.get();cookie=self.cookie_file
        self.worker(lambda:resolve_input(raw,limit,expand,self.use_chrome,cookie,self.stop,self.emit),'parsed')

    def start(self):
        if not self.list_is_current():return
        # Normalize tracking-only changes before the base provenance guard.
        self.parsed_raw=self.input.get('1.0','end').strip()
        self.operation='download'
        super().start()

    def check_qualities(self,force=False):
        if not self.list_is_current():return
        self.operation='quality';super().check_qualities(force)

    def selected_key(self):
        selected=self.table.selection();focus=self.table.focus()
        return focus if focus in selected else (selected[0] if selected else None)

    def select_unfinished(self,initial=False):
        if self.running:return
        keys=[k for k in self.table.get_children() if self.download_states.get(k) not in ('已完成','已存在')
              or not self.download_paths.get(k) or not Path(self.download_paths[k]).is_file()]
        self.table.selection_set(keys)
        if not initial:self.status.set(f'已选中 {len(keys)} 条未完成或文件缺失的任务。')

    def select_failed(self):
        if self.running:return
        self.table.selection_set([k for k in self.table.get_children() if k in self.errors or self.download_states.get(k)=='失败'])

    def select_version(self,word):
        if self.running:return
        self.table.selection_set([k for k in self.table.get_children() if word in self.table.set(k,'title')])

    def restore_paths(self):
        if not self.collection or not self.folder:return
        ids={str(e.get('id')):str(i) for i,e in enumerate(self.collection['entries'])}
        records=Path(self.folder)/'下载记录'
        if not records.is_dir():return
        for file in records.glob('*.json'):
            try:
                row=json.loads(file.read_text(encoding='utf-8'));identity=str(row.get('source_ref') or '')
                key=ids.get(identity);path=row.get('path')
                if key is None and identity.startswith('BV'):
                    key=next((ids.get(alias) for alias in core.same_video_ids(identity) if alias in ids),None)
                if key is not None and path and Path(path).is_file():
                    self.download_paths[key]=path;self.download_states[key]='已完成'
                    self.table.set(key,'status','已完成');self.table.set(key,'resolution',f"{row.get('width','?')}×{row.get('height','?')}")
            except (OSError,ValueError):pass

    def open_selected(self,locate=False):
        key=self.selected_key();path=Path(self.download_paths.get(key,''))
        if not key or not path.is_file():self.status.set('请选中已有完整文件的视频；文件已移动时需重新核对。');return
        if locate:subprocess.Popen(['explorer.exe','/select,',str(path)],creationflags=core.CREATE_NO_WINDOW)
        else:os.startfile(str(path))

    def selection_changed(self,event=None):
        super().selection_changed(event)
        key=self.selected_key();error=self.errors.get(key,'')
        category=batch.failure_help(error)['category'] if error else ''
        if '登录' in category or '权限' in category:label='更换 Cookie'
        elif '磁盘' in category:label='更换目录'
        elif '画质' in category:label='重新检查此项'
        elif '不可用' in category or '限制' in category or '受保护' in category:label='浏览器查看'
        else:label='重试选中失败'
        self.fix_button.configure(text=label)

    def fix_selected(self):
        if self.running:
            self.status.set('先点击停止，结束当前处理后再修复该项。');return
        key=self.selected_key()
        if key is None:return
        category=batch.failure_help(self.errors.get(key,''))['category']
        if '登录' in category or '权限' in category:self.choose_cookie()
        elif '磁盘' in category:self.choose_output()
        elif '画质' in category:
            self.table.selection_set(key);self.check_qualities(force=True)
        elif '不可用' in category or '限制' in category or '受保护' in category:webbrowser.open(self.collection['entries'][int(key)]['url'])
        else:self.table.selection_set(key);self.start()

    def on_event(self,kind,value):
        if kind=='parsed':
            quality=value.pop('_quality',None);error=value.pop('_error',None)
            if quality:self.emit('quality_row',{'key':'0','quality':quality})
            if error:self.emit('quality_row',{'key':'0','error':error})
            self.download_button.configure(text='下载选中视频')
            self.root.after_idle(self.after_parsed)
        elif kind=='item' and value.get('error') and '磁盘' in batch.failure_help(value['error'])['category']:
            self.stop.set();self.retry_text.set('空间不足，队列已停止；请更换目录或检查缓存。')
        elif kind=='cache_scanned':
            self.cache_plan=value;self.set_running(False)
            if not value['files']:self.status.set('没有确认可安全清理的重复缓存，未完成片段已保留。');return
            if self.messagebox.askyesno('确认清理已核验缓存',f"找到 {len(value['files'])} 个冗余源文件，可释放 {core.format_size(value['bytes'])}。\n已逐项核对有完整同画质成片。请确保其他旧窗口已停止下载。\n是否清理这些文件？",parent=self.root):
                self.operation='cache';self.worker(lambda:cache_manager.clean(value),'cache_cleaned')
        elif kind=='cache_cleaned':
            self.set_running(False);self.status.set(f"缓存清理完成：释放 {core.format_size(value['bytes'])}，清理 {value['removed']} 个文件，跳过 {value['skipped']} 个已变化/不能确认的文件。")
        elif kind in ('finished','quality_finished','error'):
            self.operation='';self.root.after_idle(self.selection_changed)

    def after_parsed(self):
        self.restore_paths();self.select_unfinished();self.persist_task()
        if self.collection:
            kind='单个视频' if self.collection.get('single_video') else ('正片版本/剧集' if self.collection.get('main_episodes_only') else '合集')
            self.status.set(f"当前清单：{self.collection['title']} · {kind} · {len(self.collection['entries'])} 条。")

    def scan_cache(self):
        if self.running:return
        self.operation='cache';self.status.set('正在核对已有成片与缓存，尚未删除文件…')
        self.worker(lambda:cache_manager.scan(self.parent.output_dir,self.stop,self.emit),'cache_scanned')

    def persist_task(self):
        super().persist_task()
        if not self.collection or self.restoring or self.history_loading:return
        try:
            self.history_dir.mkdir(parents=True,exist_ok=True)
            identifier=hashlib.sha256((str(self.parent.output_dir.resolve())+'\n'+self.collection['reference']).encode()).hexdigest()[:20]
            source=self.parent.output_dir/'下载记录'/'上次批量任务.json'
            if not source.is_file():return
            data=json.loads(source.read_text(encoding='utf-8'))
            data.update({'output_root':str(self.parent.output_dir),'updated_at':datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
            path=self.history_dir/(identifier+'.json');temp=path.with_suffix('.tmp')
            temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
        except (OSError,ValueError):self.detail.set('任务可继续，但历史记录暂未能保存。')

    def refresh_history(self):
        self.history.delete(*self.history.get_children());self.history_rows={}
        if not self.history_dir.is_dir():return
        for p in sorted(self.history_dir.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
            try:
                d=json.loads(p.read_text(encoding='utf-8'));c=d['collection'];states=d.get('states',{})
                done=sum(x in ('已完成','已存在') for x in states.values())
                self.history_rows[p.stem]=d
                self.history.insert('','end',iid=p.stem,values=(c['title'],f"{done}/{len(c['entries'])}",d['output_root'],d.get('updated_at','')))
            except (OSError,ValueError,KeyError):pass

    def import_old_tasks(self):
        """Expose older collection manifests without changing the current selection."""
        root=self.parent.output_dir
        if not (root/'合集').is_dir():return
        for records in (root/'合集').glob('*/下载记录'):
            manifests=sorted(records.glob('合集清单-*.json'),key=lambda p:p.stat().st_mtime,reverse=True)
            if not manifests:continue
            try:
                collection=json.loads(manifests[0].read_text(encoding='utf-8'))
                identifier=hashlib.sha256((str(root.resolve())+'\n'+collection['reference']).encode()).hexdigest()[:20]
                path=self.history_dir/(identifier+'.json')
                if path.exists():continue
                ids={str(e.get('id')):str(i) for i,e in enumerate(collection['entries'])}
                states={};paths={}
                for receipt in records.glob('*.json'):
                    if receipt.name.startswith('合集清单'):continue
                    try:
                        row=json.loads(receipt.read_text(encoding='utf-8'));key=ids.get(str(row.get('source_ref')))
                        if key is not None and row.get('path') and Path(row['path']).is_file():
                            states[key]='已完成';paths[key]=row['path']
                    except (OSError,ValueError):pass
                data={'version':1,'collection':collection,'selected':list(ids.values()),'states':states,'errors':{},
                      'raw':collection['reference'],'limit':'500','paths':paths,'output_root':str(root),
                      'updated_at':datetime.fromtimestamp(manifests[0].stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')}
                self.history_dir.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
            except (OSError,ValueError,KeyError):pass

    def toggle_history(self):
        if self.history_frame.winfo_manager():self.history_frame.pack_forget()
        else:
            self.refresh_history();self.history_frame.pack(fill='x',before=self.input,pady=4)

    def load_history(self):
        if self.running:self.status.set('请先停止当前处理，再恢复历史任务。');return
        selected=self.history.selection()
        if not selected:return
        data=self.history_rows[selected[0]];output=Path(data['output_root'])
        try:
            path=output/'下载记录'/'上次批量任务.json';path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        except OSError:self.status.set('历史保存目录不可写，无法恢复任务。');return
        self.history_loading=True
        self.parent.output_dir=output;self.output_text.set(str(output))
        self.restore_task(force=True);self.restore_paths();self.select_unfinished(initial=True)
        self.history_loading=False
        self.history_frame.pack_forget();self.persist_task()


def launch(root,initial_url=''):
    return UnifiedApp(root,initial_url)
