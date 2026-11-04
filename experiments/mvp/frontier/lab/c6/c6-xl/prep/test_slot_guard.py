"""No numerical jobs; verify ownership and conservative peer telemetry."""
import importlib.util,json,tempfile,time,unittest
from pathlib import Path
P=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('slotguard_tested',P/'slot_guard.py');g=importlib.util.module_from_spec(s);s.loader.exec_module(g)
class GuardTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=P);self.out=Path(self.temp.name);self.start=time.time()-1
 def tearDown(self):self.temp.cleanup()
 def test_manual_ownership(self):
  g.write(self.out/'ownership.json',dict(parent_pid=11,owned_child_pid=12));g.write(self.out/'monitor.json',dict(pid=12,peak_rss_bytes=22,status='running'))
  r=g.owned_worker('manual',self.out,11,self.start);self.assertEqual(r['pid'],12);self.assertEqual(r['stop'],str(self.out/'monitor.stop'))
  self.assertIsNone(g.owned_worker('manual',self.out,13,self.start));g.write(self.out/'monitor.json',dict(pid=99));self.assertIsNone(g.owned_worker('manual',self.out,11,self.start))
 def test_bench_requires_owned_lock_and_fresh_monitor(self):
  (self.out/'ACTIVE.lock').write_text('11');g.write(self.out/'map.monitor',dict(pid=12,peak_rss_bytes=33,status='running'))
  r=g.owned_worker('bench',self.out,11,self.start);self.assertEqual(r['pid'],12);self.assertEqual(r['stop'],str(self.out/'map.monitor.stop'))
  self.assertIsNone(g.owned_worker('bench',self.out,13,self.start));self.assertIsNone(g.owned_worker('bench',self.out,11,time.time()+10))
 def test_ambiguous_bench_refused(self):
  (self.out/'ACTIVE.lock').write_text('11')
  for n in ('a','b'):g.write(self.out/(n+'.monitor'),dict(pid=12,status='running'))
  self.assertIsNone(g.owned_worker('bench',self.out,11,self.start))
 def test_peer_peak_fresh_running_max(self):
  for n,peak,status in [('a',10,'running'),('b',20,'running'),('c',50,'timeout')]:g.write(self.out/(n+'.monitor'),dict(pid=2,peak_rss_bytes=peak,status=status))
  self.assertEqual(g.fresh_peer_peak([self.out],time.time())[0],20);self.assertEqual(g.fresh_peer_peak([self.out],time.time()+10)[0],0)
 def test_active_stale_peer_retained_until_parent_finalizes(self):
  (self.out/'ACTIVE.lock').write_text('11');g.write(self.out/'map.monitor',dict(pid=12,peak_rss_bytes=99,status='running'))
  self.assertEqual(g.fresh_peer_peak([self.out],time.time()+100)[0],99)
  g.write(self.out/'map.json',dict(guard_status='normal'))
  self.assertEqual(g.fresh_peer_peak([self.out],time.time()+100)[0],0)
if __name__=='__main__':unittest.main()
