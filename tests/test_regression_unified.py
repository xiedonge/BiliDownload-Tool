import sys,tempfile,unittest,threading,time,json
from pathlib import Path
from unittest.mock import patch
import tkinter as tk
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '工具'))
import video_downloader as c
import batch_download as b
import unified_app as u
import cache_manager as cm
URL='https://www.bilibili.com/video/BV13G41187Au'
class Tests(unittest.TestCase):
 def test_live_limit_change_releases_wait_and_cancel(self):
  controller=c.LiveRateController(100);stop=threading.Event();done=threading.Event()
  t=threading.Thread(target=lambda:(controller.pace(10000,stop),done.set()));t.start()
  time.sleep(.1);self.assertFalse(done.is_set());controller.set_limit(None)
  self.assertTrue(done.wait(.5));t.join()
  stop.set();controller.set_limit(100)
  with self.assertRaises(c.Cancelled):controller.pace(100,stop)
 def test_one_window_provenance_actions_and_history(self):
  with tempfile.TemporaryDirectory() as d:
   rootpath=Path(d);(rootpath/'settings.json').write_text(json.dumps({'output_dir':str(rootpath/'out'),'rate_limit_mb':'2','auto_check':False}))
   with patch.object(c,'APP_DIR',rootpath):
    root=tk.Tk();root.withdraw();app=u.UnifiedApp(root,URL,history_dir=rootpath/'history')
    self.assertFalse(any(isinstance(w,tk.Toplevel) for w in root.winfo_children()))
    data={'title':'测试合集','reference':URL,'truncated':False,'entries':[{'url':URL,'id':'BV13G41187Au','title':'原版'},{'url':URL+'?p=2','id':'BV13G41187Au_p2','title':'中文'}]}
    app.pending_raw=URL;app.emit('parsed',data);app.poll();root.update_idletasks()
    self.assertTrue(app.list_is_current());self.assertEqual(len(app.table.get_children()),2)
    final=rootpath/'out'/'test.mp4';final.write_bytes(b'video')
    app.emit('item',{'key':'0','state':'已完成','path':str(final)})
    app.emit('item',{'key':'1','state':'失败','error':'Cookie expired'});app.poll()
    app.select_unfinished();self.assertEqual(app.table.selection(),('1',))
    app.table.focus('1');app.selection_changed();self.assertEqual(app.fix_button.cget('text'),'重新登录 B 站')
    app.persist_task();app.refresh_history();self.assertEqual(len(app.history.get_children()),1)
    app.input.delete('1.0','end');app.input.insert('1.0',URL+'?p=3');app.input_modified()
    self.assertFalse(app.list_is_current());self.assertTrue(app.download_button.instate(['disabled']))
    app.history.selection_set(app.history.get_children()[0]);app.load_history();self.assertTrue(app.list_is_current())
    self.assertEqual(app.table.selection(),('1',))
    app.rate_mb.set('1');app.apply_rate();self.assertEqual(app.controller.limit,1048576)
    with patch.object(c,'configured_rate_limit',return_value=1048576):self.assertTrue(c.read_settings()['rate_limit_enabled'])
    root.destroy()
 def test_safe_cleanup_preserves_incomplete_and_rechecks_final(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);cache=root/'下载缓存'/'job';cache.mkdir(parents=True)
   source=cache/'title [BV13G41187Au].f30120.mp4';source.write_bytes(b'source')
   audio=cache/'title [BV13G41187Au].f30280.m4a';audio.write_bytes(b'audio')
   partial=cache/'title [BV13G41187Au].mp4.part';partial.write_bytes(b'partial')
   final=root/'title [BV13G41187Au] [3840x2160].mp4';final.write_bytes(b'final')
   media={'width':3840,'height':2160,'fps':60,'duration':10,'audio_tracks':1}
   with patch.object(c,'find_media_tools',return_value=(None,None)),patch.object(c,'probe_media',return_value=media):
    plan=cm.scan(root);self.assertEqual(len(plan['files']),2)
    final.write_bytes(b'changed')
    result=cm.clean(plan);self.assertEqual(result['removed'],0)
    plan=cm.scan(root);result=cm.clean(plan);self.assertEqual(result['removed'],2)
   self.assertTrue(final.exists());self.assertTrue(partial.exists());self.assertFalse(source.exists())
 def test_single_and_season_resolution(self):
  with patch.object(c,'download_video',return_value={'id':'BV13G41187Au','title':'单个标题'}) as dl:
   result=u.resolve_input(URL,500,False,False,None,threading.Event(),lambda *a:None)
   self.assertEqual(len(result['entries']),1);self.assertEqual(result['title'],'单个标题')
  with patch.object(b,'enumerate_collection',return_value={'entries':[{},{}]}) as enum:
   u.resolve_input('https://www.bilibili.com/bangumi/play/ss33343',500,False,False,None,threading.Event(),lambda *a:None)
   enum.assert_called_once()
if __name__=='__main__':unittest.main(verbosity=2)
