"""Local-only SpeechBrain embedding worker. PCM is transient; only vectors leave this process."""
import os,sys,json,base64,time
os.environ.setdefault('HF_HUB_DISABLE_PROGRESS_BARS','1')
os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY','1')
import numpy as np
import torch
from speechbrain.inference.speaker import EncoderClassifier
torch.set_num_threads(4)
cache=os.path.expanduser('~/.config/pixel-voice/voice-match/model')
model=EncoderClassifier.from_hparams(source='speechbrain/spkrec-ecapa-voxceleb',savedir=cache,run_opts={'device':'cpu'})
print(json.dumps({'ready':True}),flush=True)
for line in sys.stdin:
 try:
  q=json.loads(line);started=time.monotonic();raw=base64.b64decode(q['pcm'],validate=True)
  if len(raw)<64000 or len(raw)>512000 or len(raw)%2:raise ValueError('Speak for at least two seconds and at most sixteen seconds')
  x=np.frombuffer(raw,dtype='<i2').astype(np.float32)/32768
  frames=x[:len(x)//640*640].reshape(-1,640);energy=np.sqrt(np.mean(frames*frames,axis=1))
  active=energy>max(.006,min(.02,float(np.quantile(energy,.2))*2.5))
  voiced=float(np.sum(active)*.04)
  if voiced<1.4:raise ValueError('Not enough clear speech; speak a longer sentence')
  indices=np.where(active)[0];x=x[max(0,(indices[0]-3)*640):min(len(x),(indices[-1]+4)*640)]
  with torch.inference_mode():v=model.encode_batch(torch.from_numpy(x).unsqueeze(0)).squeeze().numpy()
  v=v/np.linalg.norm(v)
  print(json.dumps({'id':q['id'],'embedding':v.tolist(),'voicedSeconds':round(voiced,2),'inferenceMs':round((time.monotonic()-started)*1000)}),flush=True)
 except Exception as e: print(json.dumps({'id':locals().get('q',{}).get('id'),'error':str(e)}),flush=True)
