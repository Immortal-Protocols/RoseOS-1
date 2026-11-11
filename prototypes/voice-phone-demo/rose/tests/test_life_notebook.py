"""Server notebook durability, cancellation races, and production executor."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from rose.life import Diary, Life, verified_result
from rose.life_executor import ServerResearchExecutor, ExecutionUnavailable, ResearchStopped
from rose.life_notebook import Notebook

JOB = dict(id='synthetic-task', interest='photography', started=1000., deadline=1120.)
BODY = 'A public finding.\nUnicode café and exact whitespace.  '


class Research:
    def run(self, job, check, started):
        check()
        if not started(): raise ResearchStopped()
        return dict(summary='Read about exposure.', body=BODY, unfinished='',
                    sources=[dict(url='https://example.com/photo', title='example.com', read_at=1000.)])


class ServerNotes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.notes = Notebook(self.root/'notes', clock=lambda: 1000.)
        self.executor = ServerResearchExecutor(self.root, notebook=self.notes, research=Research(), clock=lambda: 1000.)
        self.cancelled = threading.Event()

    def tearDown(self): self.tmp.cleanup()

    def run_job(self): return self.executor.research(dict(JOB), self.cancelled, lambda: True)

    def test_real_storage_exact_readback_and_process_restart(self):
        result = self.run_job()
        self.assertIsNotNone(verified_result(result, JOB, 1000.))
        note = self.notes.read(JOB['id'])
        self.assertEqual(note['body'], BODY)
        self.assertEqual(note['sha256'], hashlib.sha256(BODY.encode()).hexdigest())
        self.assertNotIn('workspace_id', note)
        code = 'import json,sys;from rose.life_notebook import Notebook;print(json.dumps(Notebook(sys.argv[1]).read(sys.argv[2])))'
        fresh = json.loads(subprocess.check_output([sys.executable, '-c', code, str(self.notes.directory), JOB['id']], text=True))
        self.assertEqual(note, fresh)
        self.assertEqual(self.notes.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.notes.path.stat().st_mode & 0o777, 0o600)

    def test_immutable_idempotence_and_conflict(self):
        self.run_job(); before = self.notes.read(JOB['id'])
        self.assertEqual(self.notes.put(JOB['id'], JOB['interest'], BODY), before)
        with self.assertRaises(ValueError): self.notes.put(JOB['id'], JOB['interest'], BODY+'changed')
        self.assertEqual(self.notes.read(JOB['id']), before)

    def test_bounds_and_quota_never_delete_existing_notes(self):
        for tid,title,body in [('../escape','title',BODY),('ok','',BODY),('ok','title','x'*16001),('ok','title','\ud800')]:
            with self.assertRaises((ValueError,UnicodeError)): self.notes.put(tid,title,body)
        for i in range(256):self.notes.put('note'+str(i),'title','body')
        with self.assertRaises(ValueError):self.notes.put('overflow','title','body')
        self.assertEqual(self.notes.read('note0')['body'],'body')

    def test_competing_writes_have_one_immutable_result(self):
        notes=[];errors=[]
        def put():
            try:notes.append(self.notes.put('same','title',BODY))
            except Exception as e:errors.append(type(e).__name__)
        workers=[threading.Thread(target=put) for _ in range(2)]
        for w in workers:w.start()
        for w in workers:w.join(3)
        self.assertEqual(errors,[]);self.assertEqual(len(notes),2);self.assertEqual(notes[0],notes[1])

    def test_isolation_refusal_pauses_future_admission(self):
        diary=Diary(self.root/'diary');diary.interest('photography',1);diary.set_enabled(True)
        def refused(*a):raise ExecutionUnavailable('research_isolation_unavailable')
        self.executor.researcher.run=refused
        life=Life(diary,self.executor,clock=lambda:1000.,idle_seconds=1)
        self.assertTrue(life.tick(connected=False));life._worker.join(3)
        state=diary.snapshot()
        self.assertFalse(state['enabled']);self.assertEqual(state['activities'][0]['reason'],'research_isolation_unavailable')

    def test_corrupt_storage_is_not_verified(self):
        self.run_job()
        with sqlite3.connect(self.notes.path) as db: db.execute('UPDATE notes SET body=?', ('corrupt',))
        with self.assertRaises(ValueError): self.notes.read(JOB['id'])

    def test_symlinks_refused_without_touching_target(self):
        link=self.root/'link';link.symlink_to(self.notes.directory, target_is_directory=True)
        with self.assertRaises(OSError): Notebook(link)
        target=self.root/'target';target.write_text('untouched');os.chmod(target,0o644)
        other=self.root/'other';other.mkdir();(other/'notebook.sqlite3').symlink_to(target)
        with self.assertRaises(OSError): Notebook(other)
        self.assertEqual(target.read_text(),'untouched');self.assertEqual(target.stat().st_mode&0o777,0o644)

    def test_cancellation_before_commit_rolls_back(self):
        calls=[]
        def check():
            calls.append(1)
            if len(calls)==3: raise ResearchStopped()
        with self.assertRaises(ResearchStopped): self.notes.put('cancelled','Title',BODY,check)
        self.assertIsNone(self.notes.read('cancelled'))

    def test_no_phone_dependency_and_no_fabricated_workspace(self):
        self.assertFalse(self.executor.requires_phone)
        self.assertIsNone(self.executor.binding(JOB['id']))
        self.run_job()

    def test_cancel_or_expiry_before_research_writes_nothing(self):
        self.cancelled.set()
        with self.assertRaises(ResearchStopped): self.run_job()
        self.cancelled.clear();self.executor.clock=lambda:1120.
        with self.assertRaises(ResearchStopped): self.run_job()
        self.assertIsNone(self.notes.read(JOB['id']))

    def test_lost_commit_reply_is_read_once_without_second_write(self):
        original=self.notes.put;calls=[]
        def put(*a):
            calls.append(1);original(*a);raise OSError('lost reply')
        with patch.object(self.notes,'put',side_effect=put): result=self.run_job()
        self.assertEqual(len(calls),1);self.assertTrue(result['server_note']['verified'])

    def test_readback_failure_remains_uncertain(self):
        with patch.object(self.notes,'read',side_effect=OSError('unavailable')):
            with self.assertRaises(ExecutionUnavailable) as e:self.run_job()
        self.assertTrue(e.exception.uncertain)

    def test_cancel_after_commit_retains_verified_note_without_claiming_completion(self):
        diary=Diary(self.root/'diary');diary.interest('photography',1);diary.set_enabled(True)
        job=diary.claim(1000.,1,1800,120);original=self.notes.put
        def put(*a):
            result=original(*a);self.cancelled.set();return result
        life=Life(diary,self.executor,clock=lambda:1000.);life._job=job
        with patch.object(self.notes,'put',side_effect=put):life._run(job,self.cancelled)
        row=diary.snapshot()['activities'][0]
        self.assertEqual(row['state'],'interrupted');self.assertTrue(row['result']['server_note']['verified'])
        fresh=Life(Diary(diary.directory),ServerResearchExecutor(self.root,notebook=Notebook(self.notes.directory)))
        self.assertEqual(fresh.call({'action':'note','task_id':job['id']})['note']['body'],BODY)

    def test_scheduler_runs_without_phone_and_unknown_shutdown_stays_failed(self):
        diary=Diary(self.root/'diary');diary.interest('photography',1);diary.set_enabled(True)
        life=Life(diary,self.executor,clock=lambda:1000.,idle_seconds=1)
        self.assertTrue(life.tick(connected=False));life._worker.join(3)
        self.assertEqual(diary.snapshot()['activities'][0]['state'],'completed')

    def test_targeted_stop_waits_for_actual_worker(self):
        entered=threading.Event();release=threading.Event()
        def research(job,check,started):
            started();entered.set();release.wait(3);check()
        self.executor.researcher.run=research
        errors=[]
        def run():
            try:self.run_job()
            except ResearchStopped:errors.append('stopped')
        worker=threading.Thread(target=run);worker.start();self.assertTrue(entered.wait(2))
        self.assertFalse(self.executor.cancel('another-task'))
        self.assertFalse(self.executor.cancel(JOB['id']))
        release.set();worker.join(3)
        self.assertEqual(errors,['stopped']);self.assertIsNone(self.notes.read(JOB['id']))


if __name__ == '__main__': unittest.main()
