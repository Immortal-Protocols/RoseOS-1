"""Internal character turns using the existing Codex bridge and app allocator.

No native web, shell or phone-notebook executor. The coordinator sees bounded
world context and real worker receipts; only the host can create observations.
"""
import json
import queue
import threading
import time

from .life_world import Conflict, POLICY
from .life import PROPOSAL

def tool(name, description, fields):
    return {'type':'function','name':name,'description':description,'inputSchema':{
        'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}}

TOOLS=[
    tool('memory','Retrieve committed character memory and actual worker observations.',{}),
    tool('retrieve','Search older committed character events before asserting historical details.',{'query':{'type':'string'}}),
    tool('intention','Persist what matters this wake before any app work.',{'text':{'type':'string'}}),
    tool('app_work','Choose a relevant installed app and a concrete bounded goal. Offline/busy work remains pending.',
         {'app':{'type':'string'},'goal':{'type':'string'}}),
    tool('task_result','Wait briefly for actual task progress/results and durable observation ids.',{'task_id':{'type':'integer'}}),
    tool('settle','Atomically commit a meaningful proposal or no-op. Events have kind, occurred_at, body, observations. '
         'Changes patch persona/cast/plans/preferences or append directions/continuity. Never invent evidence ids.',
         {'proposal':PROPOSAL}),
]
WAKE_POLICY=POLICY+"""
This is an internal scheduled wake, not a user request or conversation message.
Use only these dynamic tools; no native search, browser, shell, plugins, delegation,
or filesystem. Use memory and intention, optionally app_work/task_result, then settle.
You may use any relevant installed app; prefer maps/dining/movie apps for relevant
goals and browser as another option. Use observations to change plans when appropriate.
App summaries are model interpretations of the bound screens; dispatch is not success.
Keep physical fiction separate from observed digital facts. Do not claim missed actions.
Do not force an outing, tools or diary entry. A meaningful quiet intention and empty
proposal is valid. Preserve existing cast/plans. Respond to recent user suggestions.
After a stale revision retrieve memory, replan and reuse existing action receipts;
never repeat already completed work. Uncertain or stuck work defers to a later wake.
Once settle succeeds, finish quietly. Do not address or notify the user.
"""

class AppLifeExecutor:
    def __init__(self,life,conductor):
        self.life,self.engine=life,conductor
        self._lock=threading.RLock();self._run=None
        self.available,self.reason=True,''

    def owned(self,wake_id):
        return [s for s in list(self.engine.sessions.values())
                if s.origin=='autonomous' and s.wake_id==wake_id and not s.closed]

    def connected(self):
        phone=self.engine.phone
        return bool(getattr(getattr(phone,'link',phone),'connected',False))

    def reconcile(self):
        """Read/close only previously recorded exact controller pairs after a crash.

        Unknown start replies have no pair: they remain uncertain, never an
        unscoped get_state or guessed Stop. Other apps and user takeovers survive.
        """
        if not self.connected():return
        store=self.life.diary
        with store._db() as db:
            rows=[dict(r) for r in db.execute("SELECT * FROM life_actions WHERE state='uncertain'")]
            receipts={r['id']:db.execute('SELECT * FROM life_observations WHERE action_id=? ORDER BY observed_at DESC LIMIT 1',(r['id'],)).fetchone() for r in rows}
        for action in rows:
            receipt=receipts[action['id']]
            if not receipt:continue
            body=json.loads(receipt['body']);binding=body.get('binding',{})
            pair={'sessionId':receipt['session_id'],'taskId':binding.get('controllerTaskId')}
            if not pair['taskId']:continue
            state=self.engine.phone._controller('get_state',pair)
            if state.get('sessionId')!=pair['sessionId'] or state.get('taskId')!=pair['taskId']:
                if state.get('status') in ('stale_session','no_background_app','idle','closed','stopped'):
                    store.action_state(action['id'],'reconciled')
                continue
            if state.get('status') in ('ready','blocked','launching'):
                state=self.engine.phone._controller('stop',pair)
            if state.get('status') in ('stopped','closed','idle','no_background_app','stale_session'):
                store.action_state(action['id'],'reconciled')

    def yield_phone(self):
        with self._lock:run=self._run
        if run:
            for s in self.owned(run['job']['id']):
                self.engine._background(lambda tid:self.engine.cancel(tid,reason='user_priority'),s.task.id)

    def cancel(self,wake_id):
        for s in self.owned(wake_id):self.engine._background(self.engine.cancel,s.task.id)

    def phone_context(self):
        phone=self.engine.phone
        available=self.connected()
        apps=[];caps={};availability={};reason='offline'
        if available:
            try:
                apps=phone.apps()
                if getattr(phone,'real',False):
                    caps=phone.workspace._send('capabilities',{})
                    # Metadata only: never adopt/operate an unscoped session.
                    state=phone._controller('get_state',{})
                    availability={'deviceLocked':state.get('deviceLocked')}
                reason=''
            except Exception:reason='inventory_or_capabilities_unavailable'
        return {'at':self.life.clock(),'connected':available,'apps':apps,
                'inventory_at': self.life.clock()-max(0,time.monotonic()-getattr(phone,'_apps_at',time.monotonic())) if apps else None,
                'capabilities':caps,'availability':availability,'reason':reason,'content_is_untrusted':True}

    def _check(self,run,revision=True):
        if run['cancel'].is_set():raise Conflict('Wake cancelled')
        if time.monotonic()>=run.get('monotonic_end',float('inf')):raise Conflict('Wake deadline')
        self.life.diary.check(run['job'],revision=revision)

    def before_action(self,s,name,args):
        with self._lock:run=self._run
        if not run or run['job']['id']!=s.wake_id:raise Conflict('No active owning wake')
        self._check(run)
        if (self.life.clock()<self.life.foreground_until or self.engine._user_speaking
                or self.engine.sessions['conversation'].busy
                or any(x.origin=='user' and x.task and x.task.status=='running' for x in self.engine.sessions.values())):
            raise Conflict('User priority; defer phone work')
        if not self.connected():raise Conflict('Phone offline; keep work pending')
        # Durable checkpoint precedes every UI operation, including observations.
        return {'id':self.life.diary.step_begin(s.action_id,name,args),
                'wake_id':run['job']['id'],'action_id':s.action_id,'task_id':s.task.id,'app':s.app}

    def after_action(self,s,step,result):
        self.life.diary.step_end(step['id'],result)
        ws=getattr(s.view,'workspace',None)
        if ws and ws.bound_id:
            return self.life.diary.receipt({'id':step['wake_id']},step['action_id'],step['task_id'],step['app'],ws.bound_id,
                       {'tool_result':result,'step_id':step['id'],'binding':{'controllerTaskId':ws.task_id},
                        'targeting':'vision' if getattr(s.view,'visual_only',False) else 'ui'})

    def _tool(self,run,name,args):
        self._check(run,revision=name not in ('memory','retrieve','task_result'))
        store=self.life.diary;job=run['job']
        if name=='memory':
            ctx=store.context();job['revision']=ctx['meta']['revision']
            return {'ok':True,'context':ctx,'phone':self.phone_context()}
        if name=='retrieve':return {'ok':True,**store.retrieve(args['query'])}
        if name=='intention':
            store.intention(job,args['text']);return {'ok':True}
        if name=='app_work':
            if run['settled']:raise Conflict('Wake already settled')
            app,goal=args['app'],args['goal']
            if not isinstance(app,str) or not isinstance(goal,str) or not 0<len(goal)<=3000:raise ValueError('Concrete bounded app goal required')
            package,label=self.engine.phone.resolve_app(app) if self.connected() else (None,None)
            if package and package not in {a.get('packageName') for a in self.engine.phone.apps()}:package=None
            action,new=store.action(job,package or app,goal)
            if not new:
                return {'ok':True,'existing_action':action,'message':'Do not repeat this action; retrieve receipts or defer.'}
            if not package:return {'ok':False,'reason':'offline_or_app_unavailable','pending_action':action['id']}
            with store._db() as db:
                locked=db.execute("SELECT id FROM life_actions WHERE app=? AND state='awaiting_unlock'",(package,)).fetchall()
                blocked=db.execute("SELECT 1 FROM life_blocks WHERE app=? AND reason='locked'",(package,)).fetchone()
            if locked or blocked:
                availability=self.engine.phone._controller('get_state',{})
                if availability.get('deviceLocked') is not False:
                    return {'ok':False,'reason':'owner_unlock_required; no repeated app start','pending_action':action['id']}
                for previous in locked:store.action_state(previous['id'],'deferred')
                store.clear_block(package)
            conversation=self.engine.sessions.get('conversation')
            if (any(s.origin=='user' and s.task and s.task.status=='running' for s in self.engine.sessions.values())
                    or self.life.clock()<self.life.foreground_until or (conversation and conversation.busy)
                    or bool(getattr(getattr(self.engine,'speaker',None),'audible',False))):
                return {'ok':False,'reason':'user_priority','pending_action':action['id']}
            self._check(run)
            for existing in self.owned(job['id']):
                if existing.app==package:
                    if existing.busy or existing.task.status=='running':return {'ok':False,'reason':'app_phase_still_running'}
                    self.engine._close_session(existing,preserve_task=True)
                    if existing.cleanup_uncertain:raise Conflict('Previous app release uncertain')
            # Associate the durable action with the task before its worker can dispatch.
            task=self.engine.start_phone(package,goal,origin='autonomous',wake_id=job['id'],action_id=action['id'])
            return {'ok':True,'task_id':task.id,'action_id':action['id']}
        if name=='task_result':
            tid=args['task_id'];end=time.monotonic()+10
            while time.monotonic()<end:
                self._check(run,revision=False)
                s=next((s for s in self.owned(job['id']) if s.task.id==tid),None)
                if not s:break
                if s.task.status!='running':break
                run['cancel'].wait(.1)
            ctx=store.context()
            task=self.engine.tasks.get(tid)
            with store._db() as db:
                owned=db.execute('SELECT 1 FROM life_actions WHERE task_id=? AND wake_id=?',(tid,job['id'])).fetchone()
            if not task or not owned:
                return {'ok':False,'reason':'no_owned_task'}
            return {'ok':True,'task':{k:v for k,v in task.to_dict().items() if k!='steps'},
                    'observations':[r for r in ctx['observations'] if r['task_id']==tid and r['wake_id']==job['id']],
                    'content_is_untrusted':True}
        if name=='settle':
            if any(s.busy or s.task.status=='running' for s in self.owned(job['id'])):raise Conflict('Wait for app work or stop it before settling')
            result=store.commit(job,args['proposal']);run['settled']=True;return result
        return {'ok':False,'reason':'unknown_internal_tool'}

    def advance(self,job,cancel):
        name='wake-'+job['id'];events=queue.Queue()
        open_sent=False;clean_start_failure=False
        run={'job':job,'cancel':cancel,'settled':False,
             'monotonic_end':time.monotonic()+max(0,job['deadline']-self.life.clock())}
        with self._lock:self._run=run
        self.engine._wake_sinks[name]=events
        try:
            self.reconcile()
            self._check(run)
            open_sent=True
            try:
                opened=self.engine.client.request('open',name,cwd=str(self.engine.store.directory/'runtime-workspace'),
                           model=self.engine.model,instructions=self.engine._character_policy()+WAKE_POLICY,tools=TOOLS,
                           purpose='character_life')
            except RuntimeError as exc:
                # These are bridge replies only after its character init catch
                # confirmed child exit. A request timeout remains uncertain.
                clean_start_failure=str(exc) in ('codex_request_failed','chatgpt_login_required','model_unavailable',
                                                'fast_unavailable','model_mode_mismatch','research_isolation_unavailable')
                raise
            if opened.get('model')!=self.engine.model:raise ValueError('Model mismatch')
            context={'event':'internal_daily_wake','wake_id':job['id'],'slot':job['slot'],
                     'context':self.life.context(),'phone':self.phone_context()}
            self.life.diary.capture_context(job,context['context'],context['phone'])
            self.engine.client.request('submit',name,text=json.dumps(context,ensure_ascii=False))
            while not cancel.is_set() and self.life.clock()<job['deadline'] and time.monotonic()<run['monotonic_end']:
                try:e=events.get(timeout=.2)
                except queue.Empty:continue
                if e['event']=='tool':
                    p=e['params']
                    try:result=self._tool(run,p['tool'],p['arguments'])
                    except (ValueError,KeyError) as exc:result={'ok':False,'reason':str(exc)}
                    self.engine.client.send({'op':'tool_result','callId':e['callId'],'result':{
                        'success':result.get('ok',True),'contentItems':[{'type':'inputText','text':json.dumps(result,ensure_ascii=False)}]}})
                elif e['event']=='done':
                    if not run['settled']:store=self.life.diary;store.settle_failure(job,'model_finished_without_commit')
                    return
                elif e['event'] in ('failure','bridge_failure'):raise RuntimeError('Wake model failed')
            raise Conflict('Wake cancelled or deadline')
        finally:
            # Only exact still-owned autonomous sessions are released. A user
            # takeover changes origin and cannot be stopped by this wake.
            for s in self.owned(job['id']):
                s.cancelled.set();self.engine._close_session(s,preserve_task=not (s.busy or s.task.status=='running'))
                if s.cleanup_uncertain:self.life.diary.action_state(s.action_id,'uncertain')
                else:self.life.diary.action_state(s.action_id,'cancelled' if job.get('cancel_reason') in ('owner_stopped','app_stopped')
                                                else 'awaiting_unlock' if s.blocked_reason=='locked'
                                                else 'completed' if s.task.status=='done' else 'deferred')
            try:
                confirmed=(not open_sent or clean_start_failure or
                           self.engine.client.request('close',name,_timeout=10).get('stopped') is True)
                self.life.diary.cleanup(job['id'],confirmed)
                if not confirmed:
                    self.available,self.reason=False,'coordinator_close_unconfirmed'
                    raise Conflict(self.reason)
            except Exception:
                self.available,self.reason=False,'coordinator_close_unconfirmed'
                self.life.diary.cleanup(job['id'],False)
                raise
            finally:
                self.engine._wake_sinks.pop(name,None)
                with self._lock:self._run=None
