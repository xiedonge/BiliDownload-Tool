import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '工具'))
import video_downloader as c
class Tests(unittest.TestCase):
 def test_fps_correction_requires_matching_source_and_preserves_other_checks(self):
  with tempfile.TemporaryDirectory() as d:
   (Path(d)/'test [BV1ve411H7dF].f30121.mp4').write_bytes(b'test')
   raw={'width':3840,'height':2160,'fps':59.94,'duration':206.543,'audio_tracks':0}
   merged=raw|{'audio_tracks':1,'duration':206.57}
   info={'id':'BV1ve411H7dF','width':3840,'height':2160,'fps':62.5,'duration':206.633,'requested_formats':[{'format_id':'30121','ext':'mp4','vcodec':'hevc','acodec':'none'},{'vcodec':'none','acodec':'aac'}]}
   with patch.object(c,'probe_media',return_value=raw):
    adjusted=c.verified_merge_selection(merged,info,d,None)
    self.assertTrue(c.matches_selection(merged,adjusted));self.assertEqual(adjusted['fps'],59.94)
    for bad in [merged|{'fps':30},merged|{'height':1080},merged|{'audio_tracks':0},merged|{'duration':100}]:
     self.assertFalse(c.matches_selection(bad,c.verified_merge_selection(bad,info,d,None)))
   with tempfile.TemporaryDirectory() as empty:
    self.assertFalse(c.matches_selection(merged,c.verified_merge_selection(merged,info,empty,None)))
if __name__=='__main__':unittest.main()
