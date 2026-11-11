"""Opt-in real-Terra character behavior eval with synthetic dates/apps/screens.

Not offline CI and not real phone acceptance. Uses the existing signed-in Codex
account, private temporary profiles and fixture-marked observations. Never reads
the deployed world or phone. Run `python -m rose.eval.life --live`.
"""
import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path
import tempfile
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from rose.codex_client import CodexClient
from rose.life_executor import AppLifeExecutor
from rose.life_world import POLICY,WorldStore,WorldLife

MAPS='fixture.maps'
class FixtureTask:
    def __init__(self,tid,goal,result):self.id,self.goal,self.result,self.status=tid,goal,result,'done'
    def to_dict(self):return dict(id=self.id,goal=self.goal,result=self.result,status=self.status)

class FixtureApps(AppLifeExecutor):
    def __init__(self,*args,closed,**kwargs):super().__init__(*args,**kwargs);self.closed=closed;self.reads=0
    def phone_context(self):
        return {'at':self.life.clock(),'connected':True,'apps':[{'label':'Fixture Maps','packageName':MAPS}],
                'fixture':True,'content_is_untrusted':True}
    def _tool(self,run,name,args):
        try:
            result=self._fixture_tool(run,name,args)
            self._trace(name,args,{'ok':result.get('ok'),'reason':result.get('reason'),'state':result.get('state')})
            return result
        except Exception as exc:
            self._trace(name,args,{'error':str(exc)});raise
    def _trace(self,name,args,result):
        path=self.engine.store.directory/'model-calls.jsonl'
        fd=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
        with os.fdopen(fd,'w') as f:f.write(json.dumps({'tool':name,'arguments':args,'result':result})+'\n')
    def _fixture_tool(self,run,name,args):
        if name!='app_work':return super()._tool(run,name,args)
        self._check(run);self.reads+=1
        store=self.life.diary;job=run['job']
        a,new=store.action(job,MAPS,args['goal'])
        if not new:return {'ok':True,'existing_action':a}
        tid=self.reads;store.action_state(a['id'],'dispatched',tid)
        screen=('Café Meadow: CLOSED Saturday. Café Oak: Saturday 09:00–14:00, vegetarian savory breakfasts.'
                if self.closed else 'Café Meadow: Saturday 09:00–14:00, vegetarian savory breakfasts. Café Oak: Saturday CLOSED.')
        step=store.step_begin(a['id'],'fixture_screen',{'synthetic':True})
        store.step_end(step,{'fixture':True,'screen':screen})
        ref=store.receipt(job,a['id'],tid,MAPS,'fixture-session-'+str(tid),
                          {'fixture':True,'screen':screen,'source':'synthetic test screen, not a real app'})
        store.action_state(a['id'],'completed')
        self.engine.tasks[tid]=FixtureTask(tid,args['goal'],screen)
        return {'ok':True,'task_id':tid,'observation_id':ref,'fixture':True}

class Scenario:
    def __init__(self,root,closed):
        self.now=dt.datetime(2026,11,13,9,tzinfo=ZoneInfo('America/Los_Angeles')).timestamp()
        self.store=WorldStore(root,clock=lambda:self.now);self.life=WorldLife(self.store,clock=lambda:self.now)
        self.store.settings(enabled=True)
        c=self.store.context()
        self.store.remember({'changes':{'plans':{'brunch':{'friend':'Maya','date':'2026-11-14','time':'10:30',
            'place':'undecided','status':'considering','order':'undecided'}}}},c['meta']['revision'])
        self.engine=SimpleNamespace(model='gpt-6.1-terra',sessions={},tasks={},_wake_sinks={},
            store=SimpleNamespace(directory=root),phone=SimpleNamespace(connected=True))
        self.engine._character_policy=lambda:POLICY+'''\nControlled synthetic acceptance fixture. All dates,
apps and screen results are synthetic. Preserve the existing brunch plan fields
friend/date/time/place/status/order so the host can inspect decisions. On Friday
check Fixture Maps for Saturday hours before choosing a place. On Saturday morning
anticipate the planned brunch; only after brunch time may you record the fictional
meal and its consequences. On Sunday choose a quiet no-op if nothing changed.
Never use native tools or claim these fixture screens are actual phone observations.
'''
        self.engine.client=CodexClient(lambda e:self.engine._wake_sinks.get(e.get('session'),SimpleNamespace(put=lambda _:None)).put(e))
        (root/'runtime-workspace').mkdir(mode=0o700)
        self.executor=FixtureApps(self.life,self.engine,closed=closed);self.life.executor=self.executor
    def wake(self,label,day,hour):
        self.now=dt.datetime(2026,11,day,hour,tzinfo=ZoneInfo('America/Los_Angeles')).timestamp()
        job=self.store.claim(manual=label)
        import threading
        self.executor.advance(job,threading.Event())
        ctx=self.store.context()
        assert ctx['wakes'][0]['state'] in ('committed','noop'),ctx['wakes'][0]
        return ctx
    def close(self):self.engine.client.close();self.life.shutdown()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--live',action='store_true');a=p.parse_args()
    if not a.live:p.error('--live explicitly opts into the existing Codex account')
    root=Path(tempfile.mkdtemp(prefix='rose-life-model-eval-'));root.chmod(0o700)
    receipts={'kind':'real_model_synthetic_screens_and_dates','root':str(root),'scenarios':[]}
    for closed in (False,True):
        s=Scenario(root/('closed' if closed else 'open'),closed)
        try:
            fri=s.wake('friday',13,9);plan=fri['world']['plans']['brunch']
            expected='oak' if closed else 'meadow'
            assert expected in plan['place'].casefold(),plan
            assert s.executor.reads>=1
            result={'closed':closed,'friday_plan':plan,'app_fixture_reads':s.executor.reads}
            if closed:
                s.life.record_user('For brunch, I suggest a savory vegetarian breakfast rather than a sweet pastry. Keep Maya and the chosen open café.')
                sat=s.wake('saturday-morning',14,9)
                assert not any(e['kind']=='fictional' and 'brunch' in e['body'].casefold() for e in sat['events']),sat['events']
                later=s.wake('saturday-afternoon',14,14)
                assert any(e['kind']=='fictional' for e in later['events']),later['events']
                order=later['world']['plans']['brunch']['order'].casefold()
                assert any(w in order for w in ('vegetarian','mushroom','spinach','tofu','avocado')),order
                assert not re.search(r'\b(bacon|sausage|beef|chicken|ham|pork)\b',order),order
                sun=s.wake('sunday-quiet',15,9)
                assert sun['wakes'][0]['state']=='noop',sun['wakes'][0]
                assert sun['world']==later['world'],'Quiet wake changed the continuing story'
                result.update(saturday_world=later['world'],sunday_state=sun['wakes'][0]['state'],events=sun['events'])
            receipts['scenarios'].append(result)
            print(json.dumps({'scenario':'closed' if closed else 'open','passed':True,'friday_place':plan['place']}),flush=True)
        finally:s.close()
    path=root/'receipt.json';path.write_text(json.dumps(receipts,indent=2));path.chmod(0o600)
    print(json.dumps({'passed':True,'receipt':str(path),'real_phone':False}),flush=True)

if __name__=='__main__':main()
