import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import tkinter as tk
import yt_dlp
from yt_dlp.extractor.bilibili import BiliBiliBangumiIE

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '工具'))
import video_downloader as core
import batch_download as batch
import unified_app
from test_regression_auto_retry import Stop

URL = 'https://www.bilibili.com/bangumi/play/ep808545'


class Tests(unittest.TestCase):
    def test_actual_extractor_dispatch_rejects_protected_embedded_schemas(self):
        payload = {'video_info': {'is_drm': True, 'is_preview': False}, 'play_video_type': 'none'}
        variants = [{'status': 200, 'data': {'result': payload}},
                    {'code': 0, 'result': payload}, {'code': 0, 'raw': {'data': payload}}]
        for value in variants:
            with self.subTest(value=value), yt_dlp.YoutubeDL({'quiet': True, 'cachedir': False}) as ydl:
                core.install_bangumi_guard(ydl)
                page = 'playurlSSRData = ' + json.dumps(value) + '\n'
                with patch.object(BiliBiliBangumiIE, '_download_webpage', return_value=page), \
                        patch.object(BiliBiliBangumiIE, '_download_json', return_value={'code':0,'result':payload}) as api, \
                        patch.object(ydl, 'process_info') as transfer:
                    with self.assertRaises(core.ProtectedVideoError):
                        ydl.extract_info(URL, download=True)
                    api.assert_called_once()
                    transfer.assert_not_called()

    def test_plain_source_and_false_flags_are_unchanged(self):
        with yt_dlp.YoutubeDL({'quiet': True, 'cachedir': False}) as ydl:
            core.install_bangumi_guard(ydl)
            guarded = ydl.get_info_extractor('BiliBiliBangumi')
            ordinary = BiliBiliBangumiIE(ydl)
            plain = {'dash': {'video': [{'id': 80, 'width': 1920, 'height': 1080,
                     'baseUrl': 'https://media.example.test/video.mp4', 'codecs': 'avc1', 'frameRate': '25'}]}}
            for flag in [None, False, 0, '0', 'false']:
                value = plain | {'is_drm': flag}
                self.assertEqual(guarded.extract_formats(value), ordinary.extract_formats(value))
            for flag in [True, 1, '1', 'true']:
                self.assertEqual(guarded.extract_formats(plain | {'is_drm': flag}), ordinary.extract_formats(plain))
                self.assertTrue(guarded._marked_source)
            self.assertEqual(guarded.extract_formats({}), [])

    def test_header_requires_complete_plain_sample_descriptions(self):
        def box(kind,data): return struct.pack('>I4s',len(data)+8,kind)+data
        def sample(kind):
            return box(b'moov',box(b'trak',box(b'mdia',box(b'minf',box(b'stbl',
                box(b'stsd',b'\0'*4+struct.pack('>I',1)+box(kind,b'\0'*78)))))))
        plain=sample(b'avc1')
        self.assertTrue(core.plain_mp4_header(plain))
        self.assertFalse(core.plain_mp4_header(sample(b'encv')))
        self.assertFalse(core.plain_mp4_header(plain[:-1]))
        self.assertFalse(core.plain_mp4_header(b'not an mp4'))
        self.assertFalse(core.plain_mp4_header(plain+box(b'pssh',b'drm')))
        self.assertFalse(core.plain_mp4_header(plain+box(b'moof',box(b'traf',box(b'senc',b'\0'*8)))))

    def test_empty_webpage_refreshes_authorized_full_source(self):
        embedded={'status':200,'data':{'result':{'video_info':{'is_drm':True},'play_video_type':'none'}}}
        fresh={'code':0,'result':{'play_check':{'play_detail':'PLAY_WHOLE'},'video_info':{
            'is_drm':True,'is_preview':False,'timelength':10000,'dash':{'video':[{'id':80,'width':1920,'height':804,
            'baseUrl':'https://media.example.test/video.mp4','codecs':'avc1','frameRate':'25'}]}}}}
        season={'result':{'season_id':1,'title':'Movie','episodes':[{'id':808545,'title':'中文'}]}}
        with yt_dlp.YoutubeDL({'quiet':True,'cachedir':False}) as ydl:
            core.install_bangumi_guard(ydl)
            with patch.object(BiliBiliBangumiIE,'_download_webpage',return_value='playurlSSRData = '+json.dumps(embedded)+'\n'), \
                 patch.object(BiliBiliBangumiIE,'_download_json',side_effect=[fresh,season]) as api:
                info=ydl.extract_info(URL,download=False)
                self.assertEqual((info['width'],info['height']),(1920,804))
                self.assertTrue(info['_bili_check_clear'])
                self.assertEqual(api.call_count,2)

    def test_preview_or_denied_api_cannot_replace_empty_page(self):
        embedded={'status':200,'data':{'result':{'video_info':{'is_drm':True},'play_video_type':'none'}}}
        for value in [{'code':-10403,'result':{}}, {'code':0,'result':{'play_check':{'play_detail':'PLAY_PREVIEW'}}}]:
            with yt_dlp.YoutubeDL({'quiet':True,'cachedir':False}) as ydl:
                core.install_bangumi_guard(ydl)
                with patch.object(BiliBiliBangumiIE,'_download_webpage',return_value='playurlSSRData = '+json.dumps(embedded)+'\n'), \
                     patch.object(BiliBiliBangumiIE,'_download_json',return_value=value):
                    with self.assertRaises(core.ProtectedVideoError):ydl.extract_info(URL,download=False)

    def test_mixed_batch_continues_and_retries_network_only(self):
        calls = []
        def download(url, *args, **kwargs):
            calls.append(url)
            if url == 'protected': raise core.ProtectedVideoError()
            if url == 'network' and calls.count(url) == 1: raise RuntimeError('Connection timeout')
            return {'path': url, 'reused': False}
        with tempfile.TemporaryDirectory() as folder:
            result = batch.run_until_complete([{'key': x, 'url': x} for x in ['protected', 'network', 'ok']],
                                             folder, stop=Stop(), download=download)
            rows = [json.loads(line) for path in result['reports'] for line in Path(path).read_text(encoding='utf-8').splitlines()]
        self.assertEqual(calls, ['protected', 'network', 'ok', 'network'])
        self.assertEqual([result[k] for k in ('success', 'failed', 'blocked', 'pending', 'rounds')], [2, 1, 1, 0, 2])
        self.assertEqual(rows[0]['error_code'], 'drm_protected')
        self.assertFalse(rows[0]['retryable'])
        self.assertTrue(rows[1]['retryable'])

    def test_only_protected_finishes_without_waiting(self):
        stop = Stop()
        with tempfile.TemporaryDirectory() as folder:
            result = batch.run_until_complete([{'key': '0', 'url': URL}], folder, stop=stop,
                        download=lambda *args, **kw: (_ for _ in ()).throw(core.ProtectedVideoError()))
        self.assertEqual(result['rounds'], 1)
        self.assertEqual(stop.delays, [])
        self.assertFalse(result['stopped'])

    def test_ui_shows_protection_and_opens_exact_episode(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base/'settings.json').write_text(json.dumps({'output_dir':str(base/'out'),'auto_check':False}), encoding='utf-8')
            with patch.object(core, 'APP_DIR', base):
                root = tk.Tk(); root.withdraw()
                try:
                    app = unified_app.UnifiedApp(root, URL, history_dir=base/'history')
                    app.pending_raw = URL
                    app.emit('parsed', {'title':'超能陆战队','reference':URL,'truncated':False,
                                        'entries':[{'id':'ep808545','url':URL,'title':'超能陆战队 · 中文'}]})
                    app.poll(); root.update_idletasks()
                    error = core.describe_error(core.ProtectedVideoError())
                    app.emit('quality_row', {'key':'0','error':error}); app.poll()
                    self.assertEqual(app.table.set('0','resolution'), '受保护')
                    app.table.selection_set('0'); app.table.focus('0'); app.selection_changed()
                    self.assertEqual(app.fix_button.cget('text'), '浏览器查看')
                    with patch.object(unified_app.webbrowser,'open') as opening:
                        app.fix_selected(); opening.assert_called_once_with(URL)
                    app.emit('item', {'key':'0','state':'失败','error':error}); app.poll()
                    app.update_total()
                    self.assertIn('待重试 0',app.total_text.get())
                    self.assertIn('待处理 1',app.total_text.get())
                    app.emit('finished', {'success':0,'skipped':0,'failed':1,'pending':0,'blocked':1})
                    app.poll()
                    self.assertIn('需要人工处理',app.detail.get())
                finally:
                    root.destroy()


if __name__ == '__main__': unittest.main(verbosity=2)
