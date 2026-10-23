import json,sys,time,unittest
from pathlib import Path
from unittest import mock
root=Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '工具'))
import video_downloader as core
import batch_download as batch
import tempfile
_TEMP = tempfile.TemporaryDirectory()
WORK = Path(_TEMP.name)
URL='https://www.bilibili.com/video/BV13G41187Au'
def quality(url=URL):
    return {'id':'BV13G41187Au','title':'test','width':3840,'height':2160,'fps':60,'format_id':'30120+30280',
            'signature':core.quality_signature(url),'chrome_used':False,'cookie_file_used':False,'fallback':None,'duration':10,'has_audio':True}
class IncrementalTests(unittest.TestCase):
    def test_resolution_identity_ignores_frame_rate_but_checks_completeness(self):
        media={'width':3840,'height':2160,'fps':30,'duration':10,'audio_tracks':1}
        self.assertTrue(core.duplicate_media(media,quality()))
        self.assertFalse(core.duplicate_media(media|{'height':1080},quality()))
        self.assertFalse(core.duplicate_media(media|{'duration':1},quality()))
        self.assertFalse(core.duplicate_media(media|{'audio_tracks':0},quality()))

    def test_legacy_title_and_part_alias_recognized_without_using_cache(self):
        folder=WORK/'legacy';folder.mkdir()
        old=folder/'旧标题 [BV13G41187Au_p1] (2).mp4';old.write_bytes(b'test')
        cache=folder/'下载缓存';cache.mkdir()
        (cache/'旧标题 [BV13G41187Au].mp4').write_bytes(b'test')
        media={'path':str(old),'width':3840,'height':2160,'fps':30,'duration':10,'audio_tracks':1}
        with mock.patch.object(core,'probe_media',return_value=media) as probe:
            self.assertEqual(core.find_existing_video(folder,quality(),Path('ffprobe')),media)
            self.assertEqual(probe.call_count,1)
            self.assertIsNone(core.find_existing_video(folder,quality()|{'id':'BV13G41187Au_p2'},Path('ffprobe')))
        self.assertEqual(core.resolution_filename(Path('测试 [BV13G41187Au].mp4'),3840,2160).name,
                         '测试 [BV13G41187Au] [3840x2160].mp4')

    def test_same_resolution_fast_skip_never_requests_network(self):
        media={'path':'existing.mp4','width':3840,'height':2160,'fps':30,'duration':10,'audio_tracks':1}
        with mock.patch.object(core,'find_media_tools',return_value=(Path('ffmpeg'),Path('ffprobe'))), \
             mock.patch.object(core,'find_existing_video',return_value=media), mock.patch('yt_dlp.YoutubeDL') as ydl:
            result=core.download_video(URL,WORK,expected_quality=quality())
            self.assertTrue(result['reused']);ydl.assert_not_called()

    def test_preview_cache_survives_reload_and_invalidates_login_change(self):
        folder=WORK/'preview'
        batch.save_quality_cache(folder,URL,quality())
        restored=batch.load_quality_cache(folder,[{'url':URL}])
        self.assertEqual(restored['0']['height'],2160)
        self.assertFalse(batch.load_quality_cache(folder,[{'url':URL}],True))
        self.assertNotIn('signature"',(folder/'下载记录'/'画质检查缓存.jsonl').read_text(encoding='utf-8'))

    def test_gui_rechecks_only_failed_missing_or_unrecognized(self):
        import tkinter as tk
        win=tk.Tk();win.withdraw()
        parent=core.DownloaderApp(win);parent.output_dir=WORK/'gui';parent.cookie_file=None
        panel=batch.BatchWindow(parent);panel.root.withdraw()
        urls=[URL,URL+'?p=2',URL+'?p=3']
        panel.collection={'title':'测试','reference':URL,'entries':[{'url':u} for u in urls]};panel.folder=WORK/'gui'
        for i in range(3):panel.table.insert('','end',iid=str(i),values=(i+1,'测试','未检查','—','待检查'))
        panel.table.selection_set(('0','1','2'))
        panel.qualities={'0':quality(),'2':quality(urls[2])|{'height':None}}
        panel.worker=lambda fn,event:fn()
        with mock.patch.object(core,'download_video',side_effect=lambda url,*a,**kw:quality(url)) as dl:
            panel.check_qualities()
            self.assertEqual([call.args[0] for call in dl.call_args_list],urls[1:])
            self.assertEqual(panel.qualities['0'],quality())
            panel.poll()
            dl.reset_mock();panel.check_qualities();dl.assert_not_called()
            panel.check_qualities(force=True);self.assertEqual(dl.call_count,3)
        panel.root.destroy();win.destroy()
if __name__=='__main__':unittest.main(verbosity=2)
