"""Local worker lifecycle and the paired compose route. Synthetic requests; no listeners or existing accounts."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from rose.remote import Remote, build_remote_app
from rose.workspace_model import WorkspaceModel, MAX_BODY

WORKER = r'''
import json,sys,time
if len(sys.argv)>1:
 from pathlib import Path
 while not Path(sys.argv[1]).exists():time.sleep(0.01)
print('{"status":"ready"}',flush=True)
for line in sys.stdin:
 r=json.loads(line)
 behavior=r.get('behavior')
 if behavior=='exit':sys.exit(1)
 if behavior=='wait':time.sleep(5)
 if behavior=='large':print('x'*40000,flush=True);continue
 print(json.dumps({'contract':'rose-layout-v1','requestId':'wrong' if behavior=='wrong' else r.get('requestId'),'status':'failed','failure':'empty_context','pid':__import__('os').getpid()}),flush=True)
'''

class WorkerTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  root=Path(self.temp.name);script=root/'worker.py';script.write_text(WORKER)
  config=root/'worker.json';config.write_text(json.dumps({'command':[sys.executable,'-u',str(script)]}))
  self.model=WorkspaceModel(config);self.addCleanup(self.model.close)
 def ready(self):
  self.model.preload();self.assertTrue(self.model.wait_ready(2),'worker did not preload')
 def test_invalid_optional_configuration_does_not_break_server_start(self):
  with patch.dict(os.environ,{'ROSE_WORKSPACE_MODEL_CONFIG':str(Path(self.temp.name)/'missing.json')}):
   model=WorkspaceModel.configured()
   self.assertEqual(model.compose({'requestId':'one'})['failure'],'model_unavailable')
   model.close()
 def test_reuses_one_worker(self):
  self.ready()
  a=self.model.compose({'requestId':'one'});b=self.model.compose({'requestId':'two'})
  self.assertEqual(a['pid'],b['pid']);self.assertEqual(b['requestId'],'two')
 def test_invalid_reply_discards_worker_and_next_call_recovers(self):
  for behavior in ('exit','large','wrong'):
   self.ready()
   self.assertEqual(self.model.compose({'requestId':'bad','behavior':behavior})['failure'],'model_unavailable')
   self.ready()
   self.assertEqual(self.model.compose({'requestId':'good'})['failure'],'empty_context')
 def test_pre_cancel_and_body_limit_do_not_start_process(self):
  self.assertEqual(self.model.compose({'requestId':'one'},lambda:True)['failure'],'cancelled')
  self.assertEqual(self.model.compose({'requestId':'one','text':'x'*MAX_BODY})['failure'],'bad_request')
  self.assertIsNone(self.model.process)
 def test_in_flight_cancel_and_busy_are_bounded(self):
  self.ready();child=self.model.process
  self.model.compose({'requestId':'preload'});stop=threading.Event();results=[]
  t=threading.Thread(target=lambda:results.append(self.model.compose({'requestId':'slow','behavior':'wait'},stop.is_set)))
  t.start();time.sleep(0.1)
  self.assertEqual(self.model.compose({'requestId':'busy'})['failure'],'model_unavailable')
  stop.set();t.join(3)
  self.assertFalse(t.is_alive());self.assertEqual(results[0]['failure'],'cancelled')
  self.assertIsNotNone(child.poll())
  self.ready()
  self.assertEqual(self.model.compose({'requestId':'recovered'})['failure'],'empty_context')
 def test_close_stops_only_its_child_and_prevents_restart(self):
  self.ready()
  self.model.compose({'requestId':'one'});child=self.model.process
  self.model.close();self.assertIsNotNone(child.poll())
  self.assertEqual(self.model.compose({'requestId':'two'})['failure'],'model_unavailable')
 def test_cold_request_returns_without_waiting_and_cancellation_does_not_kill_preload(self):
  self.model.close()
  root=Path(self.temp.name);gate=root/'loaded';config=root/'cold.json'
  config.write_text(json.dumps({'command':[sys.executable,'-u',str(root/'worker.py'),str(gate)]}))
  self.model=WorkspaceModel(config);self.addCleanup(self.model.close)
  replies=[];call=threading.Thread(target=lambda:replies.append(self.model.compose({'requestId':'cold'})))
  try:
   call.start();call.join(0.5)
   self.assertFalse(call.is_alive(),'cold request waited for model loading')
   self.assertEqual(replies[0]['failure'],'model_unavailable');self.assertFalse(self.model.ready.is_set())
   until=time.monotonic()+1
   while self.model.process is None and time.monotonic()<until:time.sleep(0.01)
   child=self.model.process;self.assertIsNotNone(child)
   self.assertEqual(self.model.compose({'requestId':'cancel'},lambda:True)['failure'],'cancelled')
   gate.touch();self.assertTrue(self.model.wait_ready(2));self.assertIs(self.model.process,child)
   self.assertEqual(self.model.compose({'requestId':'warm'})['failure'],'empty_context')
  finally:
   gate.touch();call.join(2)
 def test_close_cancels_background_loading(self):
  self.model.close()
  root=Path(self.temp.name);config=root/'cold-close.json'
  config.write_text(json.dumps({'command':[sys.executable,'-u',str(root/'worker.py'),str(root/'never-ready')]}))
  self.model=WorkspaceModel(config);self.addCleanup(self.model.close);self.model.preload()
  until=time.monotonic()+1
  while self.model.process is None and time.monotonic()<until:time.sleep(0.01)
  child=self.model.process;self.assertIsNotNone(child)
  self.model.close();self.assertIsNotNone(child.poll());self.assertFalse(self.model.ready.is_set())

async def ws(socket,**kw):await socket.close()

class RouteTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.remote=Remote(directory=Path(self.temp.name),name='test',hosts=lambda:[])
  self.key=ec.generate_private_key(ec.SECP256R1())
  public=self.key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
  self.device=hashlib.sha256(public).hexdigest();self.remote.devices.enroll(public,'phone','test','100.64.0.2')
  self.calls=[]
  model=SimpleNamespace(compose=lambda body,cancel:(self.calls.append(body) or {'contract':'rose-layout-v1','requestId':body.get('requestId'),'status':'failed','failure':'empty_context'}))
  self.server=SimpleNamespace(phone=None,serve_audio=ws,serve_events=ws,serve_phone=ws,workspace_model=model)
  self.client=TestClient(build_remote_app(self.server,self.remote))
 def headers(self):
  path='/workspace/v1/compose';stamp=str(int(time.time()*1000));nonce=base64.urlsafe_b64encode(os.urandom(16)).rstrip(b'=').decode()
  message=('rose-auth-v1\nPOST\n'+path+'\n'+stamp+'\n'+nonce+'\n'+self.remote.identity.pin).encode()
  return {'X-Rose-Device':self.device,'X-Rose-Time':stamp,'X-Rose-Nonce':nonce,'X-Rose-Signature':base64.b64encode(self.key.sign(message,ec.ECDSA(hashes.SHA256()))).decode()}
 def test_signed_snapshot_reaches_model_with_no_store_http_header(self):
  inner={'requestId':'one','snapshot':{'apps':[]}}
  body={'schemaVersion':1,'requestId':'one','request':inner}
  result=self.client.post('/workspace/v1/compose',json=body,headers=self.headers())
  self.assertEqual(result.status_code,200);self.assertEqual(result.json()['requestId'],'one')
  self.assertEqual(result.headers['cache-control'],'no-store');self.assertEqual(self.calls,[inner])
 def test_same_device_request_body_replays_first_result(self):
  body={'schemaVersion':1,'requestId':'one','request':{'requestId':'one'}}
  a=self.client.post('/workspace/v1/compose',json=body,headers=self.headers())
  b=self.client.post('/workspace/v1/compose',json=body,headers=self.headers())
  self.assertEqual(a.json(),b.json());self.assertEqual(len(self.calls),1)
 def test_changed_body_is_400_and_does_not_run_model_again(self):
  for text,status in (('one',200),('two',400)):
   body={'schemaVersion':1,'requestId':'same','request':{'requestId':'same','prompt':text}}
   self.assertEqual(self.client.post('/workspace/v1/compose',json=body,headers=self.headers()).status_code,status)
  self.assertEqual(len(self.calls),1)
 def test_pending_request_replays_and_rejects_a_different_body(self):
  entered=threading.Event();release=threading.Event();replies=[]
  def slow(body,cancel):
   self.calls.append(body);entered.set();release.wait(2)
   return {'contract':'rose-layout-v1','requestId':body['requestId'],'status':'failed','failure':'empty_context'}
  self.server.workspace_model.compose=slow
  body={'schemaVersion':1,'requestId':'same','request':{'requestId':'same'}}
  def post():replies.append(self.client.post('/workspace/v1/compose',json=body,headers=self.headers()))
  first=threading.Thread(target=post);duplicate=threading.Thread(target=post)
  with self.client:
   try:
    first.start();self.assertTrue(entered.wait(1));duplicate.start()
    conflict={**body,'request':{**body['request'],'prompt':'different'}}
    self.assertEqual(self.client.post('/workspace/v1/compose',json=conflict,headers=self.headers()).status_code,400)
   finally:
    release.set();first.join(2);duplicate.join(2)
  self.assertEqual(len(replies),2);self.assertEqual(replies[0].json(),replies[1].json());self.assertEqual(len(self.calls),1)
 def test_device_scope_prevents_cross_device_replay(self):
  body={'schemaVersion':1,'requestId':'same','request':{'requestId':'same'}}
  self.client.post('/workspace/v1/compose',json=body,headers=self.headers())
  self.key=ec.generate_private_key(ec.SECP256R1())
  public=self.key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
  self.device=hashlib.sha256(public).hexdigest();self.remote.devices.enroll(public,'other','test','100.64.0.3')
  self.client.post('/workspace/v1/compose',json=body,headers=self.headers())
  self.assertEqual(len(self.calls),2)
 def test_model_absent_is_503(self):
  self.server.workspace_model.compose=lambda body,cancel:{'contract':'rose-layout-v1','requestId':body.get('requestId'),'status':'failed','failure':'model_unavailable'}
  body={'schemaVersion':1,'requestId':'one','request':{'requestId':'one'}}
  result=self.client.post('/workspace/v1/compose',json=body,headers=self.headers())
  self.assertEqual(result.status_code,503);self.assertEqual(result.json()['status'],'unavailable')
  conflict={**body,'request':{**body['request'],'prompt':'different'}}
  self.assertEqual(self.client.post('/workspace/v1/compose',json=conflict,headers=self.headers()).status_code,400)
  self.assertEqual(self.client.post('/workspace/v1/compose',json=body,headers=self.headers()).status_code,503)
 def test_bad_envelope_never_reaches_model(self):
  for body in ({'requestId':'one'},{'schemaVersion':True,'requestId':'one','request':{'requestId':'one'}},{'schemaVersion':1,'requestId':'one','request':{'requestId':'other'}}):
   self.assertEqual(self.client.post('/workspace/v1/compose',json=body,headers=self.headers()).status_code,400)
  self.assertEqual(self.calls,[])
 def test_unsigned_or_replayed_request_never_reaches_model(self):
  path='/workspace/v1/compose'
  self.assertEqual(self.client.post(path,json={}).status_code,401)
  headers=self.headers();self.client.post(path,json={'schemaVersion':1,'requestId':'one','request':{'requestId':'one'}},headers=headers)
  self.assertEqual(self.client.post(path,json={},headers=headers).status_code,401);self.assertEqual(len(self.calls),1)
 def test_bad_json_and_large_body_are_refused(self):
  for raw in (b'not json',b'x'*(MAX_BODY+1)):
   result=self.client.post('/workspace/v1/compose',content=raw,headers=self.headers())
   self.assertEqual(result.status_code,400);self.assertEqual(result.json()['reason'],'bad_request')
  self.assertEqual(self.calls,[])
 def test_route_absent_without_model_configuration(self):
  del self.server.workspace_model
  bare=TestClient(build_remote_app(self.server,self.remote))
  self.assertEqual(bare.post('/workspace/v1/compose',json={}).status_code,404)

if __name__=='__main__':unittest.main()
