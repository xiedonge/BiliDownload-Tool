import sys,tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '工具'))
import video_downloader as c
URL='https://www.bilibili.com/video/BV13G41187Au'
class Tests(unittest.TestCase):
 def test_settings_preserved_and_validation(self):
  with tempfile.TemporaryDirectory() as d,patch.object(c,'APP_DIR',Path(d)):
   p=Path(d)/'settings.json';p.write_text(json.dumps({'cookie_file':'local-path','output_dir':'folder'}))
   c.save_rate_limit(True,'0.5');self.assertEqual(c.configured_rate_limit(),524288)
   self.assertEqual(c.read_settings()['cookie_file'],'local-path')
   c.save_rate_limit(False,'0.5');self.assertIsNone(c.configured_rate_limit())
   for x in ('NaN','inf','0','abc','-2'):
    with self.assertRaises(ValueError):c.save_rate_limit(True,x)
 def test_downloader_receives_single_worker_limit(self):
  for limit in (2097152,None):
   with patch.object(c,'configured_rate_limit',return_value=limit),patch.object(c,'find_media_tools',return_value=(Path('ffmpeg'),Path('ffprobe'))),patch('yt_dlp.YoutubeDL') as y:
    y.return_value.extract_info.return_value={'id':'test','vcodec':'h264','acodec':'aac','width':1920,'height':1080}
    c.download_video(URL,preview_only=True)
    opts=y.call_args.args[0]
    self.assertEqual(opts.get('ratelimit'),limit)
    self.assertEqual(opts['concurrent_fragment_downloads'],1 if limit else 2)
if __name__=='__main__':unittest.main()
