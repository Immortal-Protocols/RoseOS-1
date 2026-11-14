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
 def test_invalid_optional_configuration_does_not_break_server_start(self):
  with patch.dict(os.environ,{'ROSE_WORKSPACE_MODEL_CONFIG':str(Path(self.temp.name)/'missing.json')}):
   model=WorkspaceModel.configured()
   self.assertEqual(model.compose({'requestId':'one'})['failure'],'model_unavailable')
   model.close()
 def test_reuses_one_worker(self):
  a=self.model.compose({'requestId':'one'});b=self.model.compose({'requestId':'two'})
  self.assertEqual(a['pid'],b['pid']);self.assertEqual(b['requestId'],'two')
 def test_invalid_reply_discards_worker_and_next_call_recovers(self):
  for behavior in ('exit','large','wrong'):
   self.assertEqual(self.model.compose({'requestId':'bad','behavior':behavior})['failure'],'model_unavailable')
   self.assertEqual(self.model.compose({'requestId':'good'})['failure'],'empty_context')
 def test_pre_cancel_and_body_limit_do_not_start_process(self):
  self.assertEqual(self.model.compose({'requestId':'one'},lambda:True)['failure'],'cancelled')
  self.assertEqual(self.model.compose({'requestId':'one','text':'x'*MAX_BODY})['failure'],'bad_request')
  self.assertIsNone(self.model.process)
 def test_in_flight_cancel_and_busy_are_bounded(self):
  self.model.compose({'requestId':'preload'});stop=threading.Event();results=[]
  t=threading.Thread(target=lambda:results.append(self.model.compose({'requestId':'slow','behavior':'wait'},stop.is_set)))
  t.start();time.sleep(0.1)
  self.assertEqual(self.model.compose({'requestId':'busy'})['failure'],'model_unavailable')
  stop.set();t.join(3)
  self.assertFalse(t.is_alive());self.assertEqual(results[0]['failure'],'cancelled')
  self.assertIsNone(self.model.process)
 def test_close_stops_only_its_child_and_prevents_restart(self):
  self.model.compose({'requestId':'one'});child=self.model.process
  self.model.close();self.assertIsNotNone(child.poll())
  self.assertEqual(self.model.compose({'requestId':'two'})['failure'],'model_unavailable')

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
 def test_signed_snapshot_reaches_model_and_result_is_not_cached(self):
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
