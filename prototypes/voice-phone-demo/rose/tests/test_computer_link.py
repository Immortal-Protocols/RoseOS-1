"""Real voice conductor + real local Node contract, synthetic phone/model only."""
import base64
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from PIL import Image
from rose.tests.test_codex import FakeCodex, wait
from rose.bus import Bus
from rose.codex_client import CodexClient
from rose.codex_conductor import CodexConductor, PhoneTaskError
from rose.computer_link import ComputerLinkConfig
from rose.realphone import RealPhone, PhoneLink
from rose.transcripts import Transcripts


class ContractCodex(FakeCodex):
    def __init__(self, event):
        super().__init__(event)
        self.contract = CodexClient(lambda e: None)

    def request(self, op, session, **params):
        if op in ('computer_catalog','computer_command'):
            return self.contract.request(op, session, **params)
        return super().request(op, session, **params)

    def close(self):
        self.contract.close()


class ComputerPhone:
    connected = authenticated = True
    def __init__(self):
        self.calls, self.frame, self.reply = [], 0, None
        image = Image.new('RGB',(32,20),'white');out=io.BytesIO();image.save(out,format='JPEG')
        self.jpeg = base64.b64encode(out.getvalue()).decode()

    def call(self, action, **args):
        assert args.pop('paired_only') is True
        if not self.authenticated:
            return {'status':'unpaired'}
        self.calls.append((action,args))
        if self.reply is not None:
            return self.reply(action,args)
        if action == 'computer_screenshot':
            self.frame += 1
            return {'status':'ok','frameId':str(self.frame),'width':32,'height':20,'imageBase64':self.jpeg}
        return {'status':'ok' if action=='computer_status' else 'action_accepted'}


class VoiceComputer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = ComputerLinkConfig(self.root, artemis_directory=self.root/'other-artemis')
        self.enable(True)
        self.store = Transcripts(self.root/'transcripts')
        self.phone = RealPhone();self.phone.link = ComputerPhone()
        self.engine = CodexConductor(self.phone,Bus(),None,self.store,
                                     client_factory=ContractCodex,computer_link=self.config)
        self.engine.start()

    def tearDown(self):
        self.engine.stop()
        wait(lambda: all(s.closed for s in self.engine.sessions.values() if s.task))
        self.store.close();self.tmp.cleanup()

    def enable(self, value):
        (self.root/'computer-link.json').write_text(json.dumps({'enabled':value,'otherSetting':'preserved'}))

    def task(self):
        task = self.engine.start_computer('Inspect the requested computer screen')
        s = self.engine.sessions['computer-'+str(task.id)]
        self.assertTrue(wait(lambda:s.turn is not None))
        return s

    def call(self, s, name, **args):
        e={'callId':'ipc-'+str(len(self.engine.client.results)),
           'params':{'tool':name,'arguments':args,'turnId':s.turn,'callId':'call-'+name}}
        self.engine._tool(s,e)
        reply=self.engine.client.results[-1]['result']
        return json.loads(reply['contentItems'][0]['text']), reply

    def test_actual_bridge_catalog_registered_only_in_worker(self):
        opened=next(p for op,_,p in self.engine.client.calls if op=='open')
        names=[t['name'] for t in opened['tools']]
        self.assertIn('computer_task',names);self.assertNotIn('computer_click',names)
        s=self.task()
        worker=next(p for op,name,p in self.engine.client.calls if op=='open' and name==s.name)
        names=[t['name'] for t in worker['tools']]
        self.assertEqual(len([n for n in names if n.startswith('computer_')]),9)
        self.assertIn('complete_task',names);self.assertNotIn('tap_point',names)
        self.assertEqual(self.phone.link.calls,[])

    def test_screenshot_is_attached_image_not_text_or_workspace_command(self):
        s=self.task();out,reply=self.call(s,'computer_screenshot')
        self.assertTrue(out['ok']);self.assertEqual(len(reply['contentItems']),2)
        self.assertEqual(reply['contentItems'][1]['type'],'inputImage')
        self.assertNotIn('imageBase64',json.dumps(out));self.assertNotIn(self.phone.link.jpeg,json.dumps(out))
        self.assertEqual([a for a,_ in self.phone.link.calls],['computer_screenshot'])
        self.assertFalse(hasattr(s.view,'workspace'))

    def test_mutation_consumes_frame_and_completion_needs_new_observation(self):
        s=self.task();shot,_=self.call(s,'computer_screenshot')
        a={'frameId':shot['frame_id'],'x':4,'y':5}
        out,_=self.call(s,'computer_click',**a);self.assertTrue(out['ok'])
        out,_=self.call(s,'computer_click',**a);self.assertFalse(out['ok'])
        out,_=self.call(s,'complete_task',status='succeeded',summary='Finished');self.assertFalse(out['ok'])
        self.call(s,'computer_screenshot')
        out,_=self.call(s,'complete_task',status='succeeded',summary='Observed the requested result');self.assertTrue(out['ok'])
        self.assertEqual(sum(a=='computer_click' for a,_ in self.phone.link.calls),1)
        self.assertEqual(s.outcome['status'],'succeeded')

    def test_attached_image_keeps_pixel_coordinate_dimensions(self):
        s=self.task();out=io.BytesIO();Image.new('RGB',(1280,800),'white').save(out,format='JPEG')
        self.phone.link.reply=lambda a,p:{'status':'ok','frameId':'large','width':1280,'height':800,
                                         'imageBase64':base64.b64encode(out.getvalue()).decode()}
        result,reply=self.call(s,'computer_screenshot')
        self.assertEqual((result['result']['width'],result['result']['height']),(1280,800))
        data=base64.b64decode(reply['contentItems'][1]['imageUrl'].split(',',1)[1])
        with Image.open(io.BytesIO(data)) as image:self.assertEqual(image.size,(1280,800))

    def test_expired_frame_cannot_move_pointer(self):
        s=self.task();self.call(s,'computer_screenshot');s.view.frame_at-=121
        out,_=self.call(s,'computer_move',frameId='1',x=3,y=4)
        self.assertFalse(out['ok']);self.assertEqual(len(self.phone.link.calls),1)

    def test_cancel_task_closes_without_android_workspace_cleanup(self):
        s=self.task();self.assertTrue(self.engine.cancel(s.task.id))
        self.assertTrue(wait(lambda:s.closed))
        self.assertFalse(s.cleanup_uncertain);self.assertEqual(s.task.status,'cancelled')
        out,_=self.call(s,'computer_open');self.assertFalse(out['ok'])
        self.assertEqual(self.phone.link.calls,[])

    def test_timeout_consumes_mutation_frame_without_retry(self):
        s=self.task();self.call(s,'computer_screenshot')
        self.phone.link.reply=lambda a,p:{'status':'timeout'}
        out,_=self.call(s,'computer_type',frameId='1',text='draft');self.assertFalse(out['ok'])
        out,_=self.call(s,'computer_type',frameId='1',text='draft');self.assertFalse(out['ok'])
        self.assertEqual([a for a,_ in self.phone.link.calls],['computer_screenshot','computer_type'])

    def test_invalid_and_non_ascii_commands_never_reach_phone(self):
        s=self.task();self.call(s,'computer_screenshot');before=len(self.phone.link.calls)
        for name,args in [('computer_type',{'frameId':'1','text':'secret\x00'}),
                          ('computer_type',{'frameId':'1','text':'caf\u00e9'}),
                          ('computer_click',{'frameId':'1','x':-1,'y':2}),('open_app',{'app':'Maps'})]:
            out,_=self.call(s,name,**args);self.assertFalse(out['ok'])
        self.assertEqual(len(self.phone.link.calls),before)

    def test_disable_after_registration_refuses_before_transport(self):
        s=self.task();self.enable(False)
        out,_=self.call(s,'computer_open');self.assertFalse(out['ok']);self.assertEqual(self.phone.link.calls,[])
        self.assertEqual(self.engine.computer_link_status()['enabled'],False)

    def test_artemis_enable_revokes_computer_without_changing_trial(self):
        s=self.task();p=self.root/'other-artemis/trial.json';p.parent.mkdir();p.write_text('{"enabled":true}')
        before=p.read_bytes();out,_=self.call(s,'computer_open')
        self.assertFalse(out['ok']);self.assertEqual(self.phone.link.calls,[]);self.assertEqual(p.read_bytes(),before)

    def test_unpaired_link_cannot_admit_or_execute(self):
        self.phone.link.authenticated=False
        with self.assertRaises(PhoneTaskError):self.engine.start_computer('Look')
        self.assertEqual(self.engine.tasks,{})

    def test_stop_latches_before_blocked_capture_returns(self):
        s=self.task();entered=threading.Event();release=threading.Event()
        def block(action,args):
            entered.set();release.wait(3)
            return {'status':'ok','frameId':'f','width':32,'height':20,'imageBase64':self.phone.link.jpeg}
        self.phone.link.reply=block;out=[]
        t=threading.Thread(target=lambda:out.append(s.view.execute('computer_screenshot',{})))
        t.start();self.assertTrue(entered.wait(1));s.cancelled.set();release.set();t.join(2)
        self.assertFalse(t.is_alive());self.assertFalse(out[0]['ok']);self.assertIsNone(s.view.image)
        self.assertTrue(s.view.execute('computer_click',{'frameId':'f','x':1,'y':1})['ok'] is False)
        self.assertEqual(len(self.phone.link.calls),1)

    def test_phone_refusal_and_invalid_frame_are_not_success(self):
        s=self.task()
        for value in [{'status':'not_allowed'},{'status':'locked'},
                      {'status':'ok','frameId':'f','width':99,'height':20,'imageBase64':self.phone.link.jpeg}]:
            self.phone.link.reply=lambda a,p,v=value:v
            out,reply=self.call(s,'computer_screenshot')
            self.assertFalse(out['ok']);self.assertEqual(len(reply['contentItems']),1)

    def test_followup_reuses_only_computer_session(self):
        s=self.task();task=self.engine.start_computer('Continue this computer goal')
        self.assertEqual(task.id,s.task.id)
        self.assertEqual(len([s for s in self.engine.sessions.values() if s.computer]),1)
        self.assertEqual(self.phone.link.calls,[])


class OptIn(unittest.TestCase):
    def test_transport_checks_pairing_under_its_connection_lock(self):
        link=PhoneLink();link._ws=object()
        self.assertEqual(link.call('computer_open',paired_only=True)['status'],'unpaired')

    def test_missing_malformed_wrong_type_and_trials_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root);config=ComputerLinkConfig(p,artemis_directory=p/'trial')
            self.assertFalse(config.enabled())
            for text in ['{','[]','{"enabled":"true"}','{"enabled":false}']:
                (p/'computer-link.json').write_text(text);self.assertFalse(config.enabled())
            (p/'computer-link.json').write_text('{"enabled":true}');self.assertTrue(config.enabled())
            trial=p/'trial/trial.json';trial.parent.mkdir();trial.write_text('{')
            self.assertFalse(config.enabled())

    def test_disabled_start_does_not_register_computer_tools(self):
        with tempfile.TemporaryDirectory() as root:
            store=Transcripts(Path(root)/'transcripts');phone=RealPhone();phone.link=ComputerPhone()
            engine=CodexConductor(phone,Bus(),None,store,client_factory=FakeCodex,
                                 computer_link=ComputerLinkConfig(root,artemis_directory=Path(root)/'trial'))
            try:
                engine.start()
                opened=next(p for op,_,p in engine.client.calls if op=='open')
                self.assertNotIn('computer_task',[t['name'] for t in opened['tools']])
                self.assertFalse(engine.computer_link_status()['registered'])
            finally:engine.stop();store.close()
