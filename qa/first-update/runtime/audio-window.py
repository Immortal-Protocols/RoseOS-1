#!/usr/bin/env python3
"""RT-1/RT-2 helper. Given a mono/stereo 16-bit WAV of the phone/VM speaker output whose first sample
was captured at epoch START (seconds) and the backend Stop acknowledgment epoch ACK, print 50 ms RMS
frames and the speech segments that begin or continue after ACK. Speech after ACK must be only the
Stop acknowledgment (verify against the transcript); any task-result speech after ACK is a FAIL.
Usage: audio-window.py capture.wav START_EPOCH ACK_EPOCH [threshold=0.008]"""
import sys,wave,struct,math,json
wav,start,ack=sys.argv[1],float(sys.argv[2]),float(sys.argv[3]);thr=float(sys.argv[4]) if len(sys.argv)>4 else 0.008
w=wave.open(wav);n=w.getnchannels();rate=w.getframerate();step=int(rate*0.05);segs=[];cur=None;i=0
assert w.getsampwidth()==2,'16-bit PCM required'
while True:
  raw=w.readframes(step)
  if not raw:break
  s=struct.unpack('<%dh'%(len(raw)//2),raw);mono=s[::n];rms=math.sqrt(sum((x/32768)**2 for x in mono)/max(1,len(mono)))
  t=start+i*0.05;i+=1
  if rms>thr:
    cur=cur or {'start':t,'peak':0.0};cur['end']=t+0.05;cur['peak']=max(cur['peak'],rms)
  elif cur:segs.append(cur);cur=None
if cur:segs.append(cur)
after=[s for s in segs if s['end']>ack]
print(json.dumps({'threshold':thr,'ack':ack,'segments':len(segs),'segmentsEndingAfterAck':after,'lastSoundAfterAckSeconds':(max(s['end'] for s in after)-ack) if after else 0},indent=1))
