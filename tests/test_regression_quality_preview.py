import sys,threading,unittest
from pathlib import Path
from unittest import mock
root=Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '工具'))
import video_downloader as core
import batch_download as batch
URL='https://www.bilibili.com/video/BV13G41187Au'
INFO={'title':'测试','width':3840,'height':2160,'fps':60,'format_id':'30120+30280','vcodec':'h264'}
class QualityTests(unittest.TestCase):
    def test_preview_never_downloads_or_creates_output(self):
        output=root/'work'/'preview-must-not-create'
        with mock.patch('yt_dlp.YoutubeDL') as cls, mock.patch.object(core,'find_media_tools',return_value=(Path('ffmpeg.exe'),Path('ffprobe.exe'))):
            cls.return_value.extract_info.return_value=INFO.copy()
            q=core.download_video(URL,output,preview_only=True)
            self.assertEqual(q['height'],2160)
            cls.return_value.process_info.assert_not_called()
            self.assertFalse(output.exists())

    def test_quality_change_stops_before_media_download(self):
        for change in ({'height':1080,'width':1920},{'fps':30},{'format_id':'lower-bitrate'}):
            with mock.patch('yt_dlp.YoutubeDL') as cls, mock.patch.object(core,'find_media_tools',return_value=(Path('ffmpeg.exe'),Path('ffprobe.exe'))):
                cls.return_value.extract_info.return_value=INFO|change
                expected=INFO|{'signature':core.quality_signature(URL)}
                with self.assertRaisesRegex(ValueError,'画质.*不同'):
                    core.download_video(URL,root/'work'/'blocked-quality',expected_quality=expected)
                cls.return_value.process_info.assert_not_called()

    def test_changed_auth_signature_blocks(self):
        with mock.patch('yt_dlp.YoutubeDL') as cls,mock.patch.object(core,'find_media_tools',return_value=(Path('ffmpeg.exe'),Path('ffprobe.exe'))):
            cls.return_value.extract_info.return_value=INFO.copy()
            expected=INFO|{'signature':core.quality_signature(URL,True)}
            with self.assertRaisesRegex(ValueError,'登录文件已变化'):
                core.download_video(URL,root/'work'/'blocked-auth',expected_quality=expected)
            cls.return_value.process_info.assert_not_called()

    def test_gui_requires_preview_and_invalidates_on_edit(self):
        import tkinter as tk
        window=tk.Tk(); window.withdraw()
        app=core.DownloaderApp(window,URL)
        self.assertTrue(app.download_button.instate(['disabled']))
        app.start(); self.assertFalse(app.running)
        q=INFO|{'signature':core.quality_signature(URL),'chrome_used':False,'cookie_file_used':False,'fallback':None}
        app.events.put(('quality',q)); app.poll()
        self.assertFalse(app.download_button.instate(['disabled']))
        self.assertIn('3840',app.quality_label.get())
        app.use_chrome.set(True)
        self.assertIsNone(app.quality)
        self.assertTrue(app.download_button.instate(['disabled']))
        panel=batch.BatchWindow(app);panel.root.withdraw()
        panel.collection={'title':'测试','reference':URL,'entries':[{'url':URL}]}
        panel.parsed_raw=URL
        panel.qualities['0']=q
        panel.table.insert('','end',iid='0',values=(1,'测试','未检查','—','待检查'))
        panel.table.selection_set('0')
        panel.start();self.assertFalse(panel.running)
        panel.emit('quality_row',{'key':'0','quality':q});panel.poll()
        self.assertEqual(panel.table.set('0','resolution'),'3840×2160')
        panel.select_4k();self.assertEqual(panel.table.selection(),('0',))
        panel.root.destroy();window.destroy()
if __name__=='__main__':unittest.main(verbosity=2)
