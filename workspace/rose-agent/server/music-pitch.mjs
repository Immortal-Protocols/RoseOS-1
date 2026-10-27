// Monophonic sung/hummed guide -> symbolic melody. Local DSP, no audio API.
export function melodyFromPcm(base64){
 if(typeof base64!=='string'||base64.length>1400000)throw Error('Recording too long');const b=Buffer.from(base64,'base64');if(b.length<8000||b.length%2)throw Error('Sing a short melody first');
 const rate=8000,N=1024,hop=256;const samples=new Float64Array(Math.floor(b.length/4));for(let i=0;i<samples.length;i++)samples[i]=b.readInt16LE(i*4)/32768;
 const frames=[];for(let start=0;start+N<samples.length;start+=hop){let mean=0,e=0;for(let i=0;i<N;i++)mean+=samples[start+i];mean/=N;for(let i=0;i<N;i++)e+=(samples[start+i]-mean)**2;if(Math.sqrt(e/N)<.012){frames.push(null);continue;}
 let best=0,lagBest=0;const cs=[];for(let lag=12;lag<=145;lag++){let xy=0,xx=0,yy=0;for(let i=0;i<N-lag;i++){let x=samples[start+i]-mean,y=samples[start+i+lag]-mean;xy+=x*y;xx+=x*x;yy+=y*y;}const c=xy/Math.sqrt(xx*yy+1e-14);cs[lag]=c;if(c>best){best=c;lagBest=lag;}}
 if(best<.72){frames.push(null);continue;}
 // Earliest strong local maximum avoids choosing a multiple of the pitch period.
 for(let lag=13;lag<145;lag++)if(cs[lag]>.9*best&&cs[lag]>cs[lag-1]&&cs[lag]>=cs[lag+1]){lagBest=lag;break;}
 const midi=Math.round(69+12*Math.log2(rate/lagBest/440));frames.push(midi>=40&&midi<=90?midi:null);
 }
 const smoothed=frames.map((x,i)=>{const ns=frames.slice(Math.max(0,i-1),i+2).filter(x=>x!==null).sort((a,b)=>a-b);return x===null?null:ns[Math.floor(ns.length/2)];});let notes=[],current=null;for(let i=0;i<=smoothed.length;i++){let p=smoothed[i]??null;if(current&&p!==current.pitch){const duration=(i-current.frame)*hop/rate;if(duration>=.10)notes.push({pitch:current.pitch,start:current.frame*hop/rate*2,duration:duration*2,velocity:.78});current=null;}if(p!==null&&!current)current={pitch:p,frame:i};}
 if(notes.length<2)throw Error('Could not identify enough melody. Sing or hum alone, close to the phone, then try again.');const first=notes[0].start;notes=notes.map(n=>({...n,start:Math.round((n.start-first)*8)/8,duration:Math.max(.125,Math.round(n.duration*8)/8)}));const beats=Math.ceil(Math.max(...notes.map(n=>n.start+n.duration))/4)*4;return {title:'Your sung melody',summary:'Guide melody extracted locally from your recording. Timing uses a 120 BPM reference.',bpm:120,beats,tracks:[{name:'Your melody',program:0,drums:false,notes}]};
}
