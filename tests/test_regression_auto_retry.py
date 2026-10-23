import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '工具'))
import batch_download as b
class Stop:
 def __init__(self,cancel=False):self.value=False;self.delays=[];self.cancel=cancel
 def is_set(self):return self.value
 def wait(self,n):
  if n==1:
   self.delays.append(n)
   if self.cancel:self.value=True
  return self.value
class Tests(unittest.TestCase):
 def test_only_failures_retry_and_counts(self):
  calls=[];stop=Stop()
  def download(url,*args,**kw):
   calls.append(url)
   if url=='b' and calls.count('b')<3:raise RuntimeError('temporary')
   return {'path':url,'reused':url=='c'}
  with tempfile.TemporaryDirectory() as d:
   result=b.run_until_complete([{'key':u,'url':u} for u in 'abc'],d,stop=stop,download=download)
  self.assertEqual(calls,['a','b','c','b','b']);self.assertEqual(len(stop.delays),90)
  self.assertEqual([result[k] for k in ['success','skipped','failed','pending']],[2,1,0,0])
 def test_stop_during_retry_wait(self):
  calls=[];stop=Stop(True)
  def download(url,*args,**kw):calls.append(url);raise RuntimeError('persistent')
  with tempfile.TemporaryDirectory() as d:
   result=b.run_until_complete([{'key':'a','url':'a'}],d,stop=stop,download=download)
  self.assertEqual(calls,['a']);self.assertTrue(result['stopped']);self.assertEqual(result['failed'],1)
if __name__=='__main__':unittest.main()
