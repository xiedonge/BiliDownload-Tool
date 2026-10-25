import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'工具'))
import audio_download as audio
import video_downloader as core
import batch_download as batch

URL='https://www.bilibili.com/video/BV13G41187Au'
INFO={'id':'BV13G41187Au','title':'测试完整标题','ext':'m4a','format_id':'30280',
      'vcodec':'none','acodec':'aac','abr':128,'duration':1.0}


class AudioTests(unittest.TestCase):
    def test_preview_only_selects_audio_and_creates_no_files(self):
        with tempfile.TemporaryDirectory() as temp, patch('yt_dlp.YoutubeDL') as factory, patch.object(core,'find_media_tools',return_value=(Path('ffmpeg.exe'),Path('ffprobe.exe'))):
            downloader=factory.return_value.__enter__.return_value
            downloader.extract_info.return_value=INFO.copy()
            target=Path(temp)/'out'
            quality=audio.download_audio(URL,target,preview_only=True)
            self.assertEqual(factory.call_args.args[0]['format'],'ba')
            self.assertNotIn('postprocessors',factory.call_args.args[0])
            self.assertTrue(batch.valid_quality(quality,URL))
            downloader.process_info.assert_not_called()
            self.assertFalse(target.exists())

    def test_rejects_video_stream_and_changed_audio_selection(self):
        with tempfile.TemporaryDirectory() as temp, patch('yt_dlp.YoutubeDL') as factory, patch.object(core,'find_media_tools',return_value=(Path('ffmpeg.exe'),Path('ffprobe.exe'))):
            downloader=factory.return_value.__enter__.return_value
            downloader.extract_info.return_value=INFO|{'vcodec':'h264'}
            with self.assertRaisesRegex(ValueError,'独立音轨'):audio.download_audio(URL,temp)
            expected=INFO|{'audio_only':True,'signature':core.quality_signature(URL)}
            downloader.extract_info.return_value=INFO|{'format_id':'30216'}
            with self.assertRaisesRegex(ValueError,'画质已变化'):audio.download_audio(URL,temp,expected_quality=expected)
            downloader.process_info.assert_not_called()

    def test_real_audio_save_probe_duplicate_and_damaged_file(self):
        directory=os.environ.get('BILI_TEST_FFMPEG_DIR')
        ffmpeg,ffprobe=(Path(directory)/'ffmpeg.exe',Path(directory)/'ffprobe.exe') if directory else core.find_media_tools()
        tools_patch=patch.object(core,'find_media_tools',return_value=(ffmpeg,ffprobe))
        tools_patch.start();self.addCleanup(tools_patch.stop)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);fixture=root/'fixture.m4a'
            subprocess.run([str(ffmpeg),'-v','error','-f','lavfi','-i','sine=frequency=440',
                '-t','1','-c:a','aac',str(fixture)],check=True,creationflags=core.CREATE_NO_WINDOW)
            made=[]
            class FakeDownloader:
                def __init__(self,options):self.params=options;made.append(self)
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def extract_info(self,*args,**kwargs):return INFO.copy()
                def process_info(self,info):
                    path=Path(self.params['paths']['home'])/'track.m4a'
                    shutil.copyfile(fixture,path)
                    for hook in self.params['post_hooks']:hook(str(path))
            with patch('yt_dlp.YoutubeDL',FakeDownloader):
                result=audio.download_audio(URL,root/'out')
            path=Path(result['path'])
            self.assertTrue(path.exists());self.assertIn('[音频 aac 30280]',path.name)
            self.assertEqual(audio.probe_audio(path,ffprobe)['audio_tracks'],1)
            self.assertFalse(any((root/'out'/'下载缓存').rglob('*.m4a')))
            expected=INFO|{'audio_only':True,'signature':core.quality_signature(URL)}
            with patch('yt_dlp.YoutubeDL') as factory:
                reused=audio.download_audio(URL,root/'out',expected_quality=expected)
                self.assertTrue(reused['reused']);factory.assert_not_called()
            path.write_bytes(b'broken')
            self.assertIsNone(audio.existing_audio(root/'out',expected,ffprobe))
            with patch('yt_dlp.YoutubeDL',FakeDownloader):new=audio.download_audio(URL,root/'out')
            self.assertNotEqual(new['path'],str(path));self.assertEqual(path.read_bytes(),b'broken')


if __name__=='__main__':unittest.main()
