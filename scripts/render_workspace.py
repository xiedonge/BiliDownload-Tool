"""Capture only our own temporary demo window. Does not read accounts or existing tasks."""
from pathlib import Path
import sys
import json
import tempfile
from unittest.mock import patch
import tkinter as tk
from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '工具'))
import video_downloader as core
from unified_app import UnifiedApp

destination = Path(sys.argv[1]).resolve()
destination.mkdir(parents=True, exist_ok=True)
url = 'https://www.bilibili.com/video/BV13G41187Au'
with tempfile.TemporaryDirectory() as folder:
    base = Path(folder)
    (base/'settings.json').write_text(json.dumps({'output_dir': str(base/'out'), 'auto_check': False}))
    with patch.object(core, 'APP_DIR', base):
        root = tk.Tk()
        root.withdraw()
        app = UnifiedApp(root, url, history_dir=base/'history')
        root.title('新版界面预览 · 示例数据')
        app.pending_raw = url
        app.emit('parsed', {'title': '示例音乐合集', 'reference': url, 'truncated': False,
                            'entries': [{'id': f'example{i}', 'url': url+f'?p={i+1}',
                                         'title': f'现场演出精选 · 第 {i+1:02d} 集'} for i in range(12)]})
        app.poll()
        root.update_idletasks()
        for i in range(12):
            key = str(i)
            app.table.set(key, 'resolution', '3840×2160' if i%2 == 0 else '1920×1080')
            app.table.set(key, 'fps', '60' if i%2 == 0 else '30')
            app.table.set(key, 'status', '等待下载')
        app.task_keys = [str(i) for i in range(12)]
        app.batch_locked = True
        app.download_states.update({'0':'已完成','1':'已存在','2':'正在下载'})
        app.table.set('0','status','已完成')
        app.table.set('1','status','已存在同画质')
        app.table.set('2','status','正在下载')
        app.update_total()
        app.current_title.set('当前：现场演出精选 · 第 03 集')
        app.stage_text.set('下载视频 · 随后下载音频并合并')
        app.emit('progress', {'percent': 42.5, 'downloaded': 912680550, 'total': 2147483648,
                              'speed': 1887436, 'eta': 654, 'part': '视频'})
        app.poll()
        app.status.set('示例音乐合集 · 12 条；可先检查画质，再预览并确认下载。')
        app.table.selection_set('2')
        app.selection_changed()
        root.geometry('1000x700+60+60')
        root.deiconify()
        root.attributes('-topmost', True)
        root.update()
        def capture(name):
            root.update()
            x,y=root.winfo_rootx(),root.winfo_rooty()
            ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height())).save(destination/name)
        def snap(name):
            try:
                capture(name)
            finally:
                root.quit()
        root.after(400, lambda: snap('下载主界面.png'))
        root.mainloop()
        app.show_account()
        root.after(300, lambda: snap('工具内登录.png'))
        root.mainloop()
        root.destroy()
print('UI_PREVIEWS_SAVED')
