import sys,unittest,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '工具'))
import video_downloader as c
import batch_download as b
SS='https://www.bilibili.com/bangumi/play/ss33343'
EP='https://www.bilibili.com/bangumi/play/ep332611'
class Tests(unittest.TestCase):
 def test_normalization_and_identity(self):
  self.assertEqual(c.normalize_url(SS+'?from_spmid=666'),SS)
  self.assertEqual(c.video_id_from_url(EP+'?theme=movie'),'ep332611')
  self.assertIsNone(c.bangumi_id('https://example.com/bangumi/play/ss33343'))
 def test_main_versions_only_and_limit(self):
  data={'code':0,'result':{'season_id':33343,'title':'天气之子','episodes':[{'id':332611,'title':'原版'},{'id':332658,'title':'中文'}],'section':[{'episodes':[{'id':123,'title':'预告'}]}]}}
  r=b.bangumi_collection(data,500)
  self.assertEqual([x['title'] for x in r['entries']],['天气之子 · 原版','天气之子 · 中文'])
  self.assertEqual(r['expected_count'],2);self.assertFalse(r['truncated'])
  r=b.bangumi_collection(data,1);self.assertTrue(r['truncated']);self.assertEqual(len(r['entries']),1)
 def test_episode_identity_and_preview_rejection(self):
  info={'id':'332611','title':'原版','width':1920,'height':1080,'fps':24,'format_id':'1','duration':6741,'acodec':'aac','vcodec':'h264'}
  with tempfile.TemporaryDirectory() as folder,patch.object(c,'find_media_tools',return_value=(Path('ffmpeg'),Path('ffprobe'))),patch('yt_dlp.YoutubeDL') as ydl,patch.object(c,'bangumi_title',return_value='天气之子 · 原版'):
   ydl.return_value.extract_info.return_value=info.copy()
   result=c.download_video(EP,folder,preview_only=True)
   self.assertEqual(result['id'],'ep332611');ydl.return_value.process_info.assert_not_called()
   def make(options):
    options['logger'].warning('Only preview format is available')
    return instance
   instance=ydl.return_value;ydl.side_effect=make
   with self.assertRaisesRegex(ValueError,'试看'):c.download_video(EP,folder,preview_only=True)
 def test_season_routes_to_episode_selection(self):
  import tkinter as tk
  root=tk.Tk();root.withdraw();app=c.DownloaderApp(root,SS)
  with patch.object(app,'open_batch') as opening:
   app.check_quality();opening.assert_called_once();self.assertFalse(app.running)
  root.destroy()
if __name__=='__main__':unittest.main(verbosity=2)
