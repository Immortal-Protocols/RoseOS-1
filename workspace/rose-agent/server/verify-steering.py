import urllib.request,ssl,json,time
from pathlib import Path
p=(Path.home()/'.config/pixel-voice');ctx=ssl.create_default_context(cafile=str(p/'server-cert.pem'));ctx.check_hostname=False
headers={'Authorization':'Bearer '+(p/'pair-token').read_text().strip(),'Content-Type':'application/json'}
def post(text):
 r=urllib.request.Request('https://127.0.0.1:9443/task',data=json.dumps({'text':text}).encode(),headers=headers)
 return urllib.request.urlopen(r,context=ctx,timeout=50).read().decode()
print('Started:',post('Integration test only: wait 8 seconds using a shell sleep, then write rose-agent/runtime/steering-proof.txt with the text ORIGINAL. Verify the result. Only modify that file.'))
time.sleep(2)
print('Correction sent:',post('Correction to the running integration test: the final text of steering-proof.txt must be CORRECTED, not ORIGINAL. Verify the corrected text.'))
