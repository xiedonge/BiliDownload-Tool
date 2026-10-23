import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '工具'))
import video_downloader as c
class Tests(unittest.TestCase):
 def test_only_current_successful_cache_is_removed(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);cache=root/'下载缓存'/('a'*16)/('b'*8);cache.mkdir(parents=True)
   final=root/'title [BV13G41187Au].mp4';final.write_bytes(b'final')
   source=cache/'title [BV13G41187Au].f30120.mp4';source.write_bytes(b'video')
   audio=cache/'title [BV13G41187Au].f30280.m4a';audio.write_bytes(b'audio')
   other=cache/'unrelated.txt';other.write_text('keep')
   media={'width':3840,'height':2160,'fps':60,'duration':10,'audio_tracks':1}
   info=media|{'id':'BV13G41187Au','acodec':'aac'}
   with patch.object(c,'probe_media',return_value=media):
    self.assertEqual(c.clear_verified_cache(cache,root,final,info,None),10)
   self.assertFalse(source.exists());self.assertFalse(audio.exists());self.assertTrue(other.exists());self.assertTrue(final.exists())
 def test_bad_final_and_outside_scope_preserve_cache(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);cache=root/'下载缓存'/('a'*16)/('b'*8);cache.mkdir(parents=True)
   source=cache/'title [BV13G41187Au].f30120.mp4';source.write_bytes(b'video')
   final=root/'final.mp4';final.write_bytes(b'bad')
   media={'width':640,'height':360,'fps':30,'duration':10,'audio_tracks':0}
   with patch.object(c,'probe_media',return_value=media):
    with self.assertRaises(ValueError):c.clear_verified_cache(cache,root,final,{'id':'BV13G41187Au','width':3840,'height':2160,'acodec':'aac'},None)
    with self.assertRaises(ValueError):c.clear_verified_cache(root,root,final,{},None)
   self.assertTrue(source.exists())
if __name__=='__main__':unittest.main()
