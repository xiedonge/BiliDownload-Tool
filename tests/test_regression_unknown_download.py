import sys,tempfile,tkinter as tk,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '工具'))
import video_downloader as c
import batch_download as b
class Tests(unittest.TestCase):
 def test_unknown_allowed_known_stays_guarded(self):
  with tempfile.TemporaryDirectory() as d:
   root=tk.Tk();root.withdraw();parent=c.DownloaderApp(root);parent.output_dir=Path(d);parent.cookie_file=None
   panel=b.BatchWindow(parent);panel.root.withdraw()
   urls=['https://www.bilibili.com/video/BV13G41187Au?p='+str(i) for i in (1,2)]
   panel.input.insert('1.0',urls[0])
   panel.emit('parsed',{'title':'test','reference':urls[0],'truncated':False,'entries':[{'url':u,'title':'test'} for u in urls]});panel.poll()
   panel.qualities['0']={'width':3840,'height':2160,'format_id':'4k','signature':c.quality_signature(urls[0])}
   panel.worker=lambda fn,completion:fn()
   with patch.object(b,'run_until_complete',return_value={}) as run:
    panel.start();entries=run.call_args.args[0]
    self.assertIn('quality',entries[0]);self.assertNotIn('quality',entries[1])
    run.reset_mock();panel.use_chrome=True;panel.start();run.assert_not_called()
   panel.emit('item',{'key':'1','state':'已完成','width':1920,'height':1080,'fps':30});panel.poll()
   self.assertEqual(panel.table.set('1','resolution'),'1920×1080')
   panel.root.destroy();root.destroy()
if __name__=='__main__':unittest.main()
