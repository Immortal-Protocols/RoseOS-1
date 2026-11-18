"""Synthetic character continuity, transactional recovery and scheduling acceptance."""
import datetime as dt
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from rose.life import Diary
from rose.life_world import WorldStore, WorldLife, Conflict
from rose.life_profile import backup,export,import_profile,migrate_host

class World(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.now=dt.datetime(2026,11,13,8,tzinfo=ZoneInfo('America/Los_Angeles')).timestamp()
        self.store=WorldStore(self.root/'life',clock=lambda:self.now);self.store.acquire()
        self.store.settings(enabled=True)

    def tearDown(self):self.store.release();self.temp.cleanup()
    def at(self,day,hour):self.now=dt.datetime(2026,11,day,hour,tzinfo=ZoneInfo('America/Los_Angeles')).timestamp()
    def wake(self,slot=None):
        j=self.store.claim(manual=slot);self.assertIsNotNone(j);self.store.intention(j,'Continue the current plan');return j
    def commit(self,j,**changes):return self.store.commit(j,{'changes':changes,'events':[]})

    def test_friday_sunday_changed_hours_suggestion_and_quiet(self):
        self.at(13,9);fri=self.wake()
        action,_=self.store.action(fri,'maps','Check café hours')
        self.store.action_state(action['id'],'dispatched',11)
        ref=self.store.receipt(fri,action['id'],11,'maps','actual-session',{'screen':'Café is closed Saturday'})
        self.store.action_state(action['id'],'completed')
        self.store.commit(fri,{'changes':{'plans':{'brunch':{'friend':'Maya','place':'Alternate café','when':'Saturday'}}},
            'events':[{'kind':'digital','occurred_at':self.now,'body':'Maps showed the first café closed.','observations':[ref]}]})
        # User suggestion changes subsequent decisions, before another wake.
        ctx=self.store.context()
        self.store.remember({'changes':{'preferences':{'order':'savory breakfast'},'directions':['Prefer a quiet café']}},ctx['meta']['revision'])
        self.at(14,9);sat=self.wake()
        self.assertEqual(self.store.context()['world']['preferences']['order'],'savory breakfast')
        self.commit(sat,plans={'brunch':{'friend':'Maya','place':'Quiet café','when':'this morning'}})
        self.at(14,14);afternoon=self.wake()
        self.store.commit(afternoon,{'changes':{'cast':{'Maya':{'relationship':'fictional friend','detail':'disagreed about the movie'}},
            'plans':{'brunch':{'state':'finished','order':'savory breakfast'}}},'events':[
                {'kind':'fictional','occurred_at':self.now-3600,'body':'Brunch with Maya was good; the movie debate remains unfinished.','observations':[]}]})
        self.at(15,9);sun=self.wake()
        ctx=self.store.context();self.assertEqual(ctx['world']['plans']['brunch']['order'],'savory breakfast')
        self.assertIn('disagreed',ctx['world']['cast']['Maya']['detail'])
        self.assertEqual(self.store.commit(sun,{'changes':{},'events':[],'reason':'A quiet morning; no meaningful change'})['state'],'noop')
        self.assertEqual(len(self.store.context()['events']),2)

    def test_unique_slot_one_run_coalescing_pause_no_catchup(self):
        self.at(13,20);j=self.wake();self.assertEqual(j['slot'],'evening')
        self.assertIsNone(self.store.claim());self.commit(j)
        self.assertIsNone(self.store.claim())
        self.store.settings(enabled=False);self.at(15,15);self.assertIsNone(self.store.claim())
        self.store.settings(enabled=True);self.assertIsNone(self.store.claim())
        self.at(15,19);self.assertEqual(self.wake()['slot'],'evening')

    def test_revision_stop_and_atomic_rollback_reject_invented_future_evidence(self):
        j=self.wake('race')
        ctx=self.store.context();self.store.remember({'changes':{'preferences':{'coffee':'tea'}}},ctx['meta']['revision'])
        with self.assertRaises(Conflict):self.commit(j,plans={'bad':'stale'})
        j['revision']=self.store.context()['meta']['revision']
        for event in [dict(kind='digital',occurred_at=self.now,body='Invented app result',observations=['fake']),
                      dict(kind='fictional',occurred_at=self.now+1,body='Future meal',observations=[])]:
            with self.assertRaises(ValueError):self.store.commit(j,{'changes':{'plans':{'bad':'rolled back'}},'events':[event]})
            self.assertNotIn('bad',self.store.context()['world']['plans'])
        self.store.cancel(j,'owner stop')
        with self.assertRaises(Conflict):self.commit(j)
        self.assertEqual(self.store.context()['events'],[])

    def test_crash_after_action_keeps_receipts_and_never_replays(self):
        j=self.wake('crash');a,_=self.store.action(j,'maps','Read hours')
        self.store.action_state(a['id'],'dispatched',3)
        step=self.store.step_begin(a['id'],'tap_point',{'frame_id':'one'})
        self.store.step_end(step,{'ok':True,'frame_id':'two'})
        ref=self.store.receipt(j,a['id'],3,'maps','bound-1',{'frame_id':'two'})
        self.store.release();self.store=WorldStore(self.root/'life',clock=lambda:self.now);self.store.acquire()
        ctx=self.store.context();self.assertEqual(ctx['observations'][0]['id'],ref)
        self.assertEqual(ctx['pending_work'][0]['state'],'uncertain')
        j2=self.wake('after-crash')
        with self.assertRaises(Conflict):self.store.action(j2,'maps','Read hours')
        self.assertEqual(self.commit(j2)['state'],'noop')  # offline/fiction is not globally blocked

    def test_single_writer_and_copied_profile_requires_handoff(self):
        second=WorldStore(self.root/'life',clock=lambda:self.now)
        with self.assertRaises(Conflict):second.acquire()
        backup(self.root/'life',self.root/'backup')
        copied=WorldStore(self.root/'backup',clock=lambda:self.now)
        with self.assertRaises(Conflict):copied.acquire()
        self.store.release()
        manifest=export(self.root/'life',self.root/'transfer')
        with self.assertRaises(Conflict):self.store.acquire()
        import_profile(self.root/'transfer',self.root/'destination',manifest['agent_id'])
        dest=WorldStore(self.root/'destination');dest.acquire()
        try:self.assertEqual(dest.context()['meta']['agent_id'],manifest['agent_id'])
        finally:dest.release()

    def test_machine_binding_survives_hostname_change_but_not_machine_change(self):
        identity=self.store.host_id()
        with patch('rose.life_world.platform.node',return_value='different-network-name'):
            self.assertEqual(self.store.host_id(),identity)
        self.store.release()
        with patch.object(WorldStore,'machine_id',return_value='other-machine'):
            with self.assertRaises(Conflict):self.store.acquire()
        self.store.acquire()

    def test_matching_legacy_host_upgrades_without_changing_world(self):
        before=self.store.context()['world'];self.store.release()
        with self.store._db() as db:
            db.execute('UPDATE life_authority SET host=? WHERE id=1',(self.store.legacy_host_id(),))
        self.store.acquire()
        with self.store._db() as db:
            self.assertEqual(db.execute('SELECT host FROM life_authority').fetchone()[0],self.store.host_id())
        self.assertEqual(before,self.store.context()['world'])

    def test_changed_legacy_hostname_requires_explicit_exact_offline_migration(self):
        agent=self.store.context()['meta']['agent_id'];before=self.store.context()['world']
        with self.assertRaises(BlockingIOError):migrate_host(self.store.directory,agent,'original-network-name')
        self.store.release()
        with self.store._db() as db:
            db.execute('UPDATE life_authority SET host=? WHERE id=1',
                       (self.store.legacy_host_id('original-network-name'),))
        with self.assertRaises(Conflict):self.store.acquire()
        for accepted,hostname in [('wrong-agent','original-network-name'),(agent,'wrong-hostname')]:
            with self.assertRaises(ValueError):migrate_host(self.store.directory,accepted,hostname)
        backup(self.store.directory,self.root/'hostname-copy')
        with self.assertRaises(ValueError):migrate_host(self.root/'hostname-copy',agent,'original-network-name')
        migrate_host(self.store.directory,agent,'original-network-name')
        self.store.acquire()
        self.assertEqual(before,self.store.context()['world'])
        self.assertEqual(agent,self.store.context()['meta']['agent_id'])

    def test_hostname_migration_never_reactivates_exported_profile(self):
        agent=self.store.context()['meta']['agent_id'];self.store.release()
        with self.store._db() as db:
            db.execute("UPDATE life_authority SET host=?,state='exported' WHERE id=1",
                       (self.store.legacy_host_id('original-network-name'),))
        with self.assertRaises(ValueError):migrate_host(self.store.directory,agent,'original-network-name')
        with self.assertRaises(Conflict):self.store.acquire()

    def test_legacy_migration_identity_and_notes_are_preserved(self):
        old=Diary(self.root/'old');old.interest('music',1)
        with old._db() as db:db.execute("INSERT INTO activities VALUES ('legacy','music','completed',1,2,3,?,'')",(json.dumps({'summary':'Real research'}),))
        migrated=WorldStore(self.root/'old');migrated.acquire()
        try:
            stable=migrated.context()['meta']['agent_id'];self.assertEqual(migrated.snapshot()['activities'][0]['id'],'legacy')
            migrated.release();migrated=WorldStore(self.root/'old');migrated.acquire()
            self.assertEqual(migrated.context()['meta']['agent_id'],stable)
        finally:migrated.release()

    def test_migrated_profile_cannot_admit_a_second_legacy_research_scheduler(self):
        legacy=Diary(self.store.directory);legacy.interest('music',self.now-1000)
        legacy.set_enabled(True)
        self.assertIsNone(legacy.claim(self.now,1,30,60))
        self.store.settings(enabled=True)
        self.assertTrue(self.store.context()['meta']['enabled'])
        self.store.release();self.store.acquire()
        self.assertFalse(legacy.snapshot()['enabled'])

    def test_configurable_local_slots_and_dst(self):
        self.store.settings(slots={'morning':'10:15','afternoon':'15:30','evening':'21:45'})
        self.at(13,10);self.assertIsNone(self.store.claim())
        self.now+=900;j=self.wake();self.assertEqual(j['slot'],'morning')
        self.assertEqual(j['local_date'],'2026-11-13')

    def test_offline_wake_conversation_does_not_wait_and_stop_latches(self):
        life=WorldLife(self.store,clock=lambda:self.now)
        began,release=threading.Event(),threading.Event()
        class Executor:
            def advance(_,job,cancel):began.set();release.wait(2)
            def cancel(_,wake):release.set()
            def yield_phone(_):pass
        life.executor=Executor()
        self.assertTrue(life.tick(connected=False,user_busy=True,manual='offline'))
        self.assertTrue(began.wait(1));life.record_user('Try a different café')
        self.assertIn('different café',life.context()['world']['directions'][-1]['text'])
        self.assertTrue(life.stop());self.assertTrue(life.shutdown())

    def test_stop_cancels_deferred_continuations_after_wake_settles(self):
        life=WorldLife(self.store,clock=lambda:self.now)
        j=self.wake('deferred');a,_=self.store.action(j,'maps','Read café hours')
        self.commit(j)
        self.assertTrue(life.call({'action':'stop'})['ok'])
        self.assertEqual(life.context()['pending_work'],[])
        self.assertFalse(life.call({'action':'stop'})['ok'])
        life.shutdown()

    def test_actual_heard_disclosure_and_details_survive_fresh_context(self):
        life=WorldLife(self.store,clock=lambda:self.now)
        life.record_assistant('Brunch with Maya is fictional; the app checks are real.')
        self.assertTrue(life.context()['world']['fiction_established'])
        self.assertIn('Maya',life.context()['world']['continuity'][-1]['text'])
        life.shutdown()

if __name__=='__main__':unittest.main()
