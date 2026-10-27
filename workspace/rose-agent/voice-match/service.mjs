import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawn} from 'node:child_process';
import readline from 'node:readline';
import {toFile} from 'openai';
export const normalizePassword=s=>String(s).toLowerCase().normalize('NFKC').replace(/[^\p{L}\p{N}\s]/gu,' ').replace(/\s+/g,' ').trim();
export function cosine(a,b){if(a.length!==b.length||!a.length)throw Error('Invalid voice profile');let s=0,x=0,y=0;for(let i=0;i<a.length;i++){s+=a[i]*b[i];x+=a[i]*a[i];y+=b[i]*b[i];}return s/Math.sqrt(x*y);}
export class SpeakerWorker {
 constructor(directory){this.directory=directory;this.ready=false;this.pending=new Map();}
 start(){if(this.child)return;this.child=spawn(path.join(this.directory,'.venv/bin/python'),[path.join(this.directory,'speaker.py')],{stdio:['pipe','pipe','pipe']});
  this.child.stderr.on('data',()=>{});readline.createInterface({input:this.child.stdout}).on('line',line=>{try{const r=JSON.parse(line);if(r.ready){this.ready=true;return;}const p=this.pending.get(r.id);if(p){clearTimeout(p.timer);this.pending.delete(r.id);r.error?p.reject(Error(r.error)):p.resolve(r);}}catch{}});
  this.child.on('exit',()=>{this.ready=false;this.child=null;for(const p of this.pending.values()){clearTimeout(p.timer);p.reject(Error('Voice matcher unavailable'));}this.pending.clear();});
 }
 embed(pcm){this.start();if(!this.ready)throw Error('Voice matcher is warming up; try again shortly');if(typeof pcm!=='string'||pcm.length>700000)throw Error('Invalid recording');return new Promise((resolve,reject)=>{const id=crypto.randomUUID(),timer=setTimeout(()=>{this.pending.delete(id);reject(Error('Voice match timed out'));},15000);this.pending.set(id,{resolve,reject,timer});this.child.stdin.write(JSON.stringify({id,pcm})+'\n');});}
 stop(){this.child?.kill();}
}
export class VoiceMatch {
 constructor(root,{worker,client,now=Date.now}={}){this.root=root;this.worker=worker;this.client=client;this.now=now;this.attempts=new Map();this.grants=new Map();fs.mkdirSync(root,{recursive:true,mode:0o700});this.configFile=path.join(root,'config.json');}
 config(){try{return JSON.parse(fs.readFileSync(this.configFile,'utf8'));}catch{return {enabled:false,profiles:[],staged:[]};}}
 save(c){const f=this.configFile+'.tmp';fs.writeFileSync(f,JSON.stringify(c),{mode:0o600});fs.renameSync(f,this.configFile);}
 status(){const c=this.config();return {enabled:c.enabled===true,enrolled:(c.profiles?.length||0)>=3,samples:c.staged?.length||0,passwordSet:!!c.password,modelReady:!!this.worker?.ready};}
 audit(event,data={}){fs.appendFileSync(path.join(this.root,'events.jsonl'),JSON.stringify({at:this.now(),event,...data})+'\n',{mode:0o600});}
 prune(){for(const [id,x] of this.attempts)if(x.expires<this.now())this.attempts.delete(id);for(const [id,x] of this.grants)if(x.expires<this.now())this.grants.delete(id);}
 identity(token){this.prune();return this.grants.get(token)?.scope||null;}
 grant(scope){const token=crypto.randomBytes(32).toString('hex');this.grants.set(token,{scope,expires:this.now()+4*60*60*1000});this.audit('access_granted',{scope});return {scope,grant:token};}
 begin(){this.prune();if(!this.config().enabled)return {required:false};const attempt=crypto.randomBytes(24).toString('hex');this.attempts.set(attempt,{expires:this.now()+180000,checks:0,passwordTries:0});return {required:true,attempt};}
 attempt(id){this.prune();const a=this.attempts.get(id);if(!a)throw Error('Start a new voice check');return a;}
 async enroll(pcm,reset=false){const r=await this.worker.embed(pcm),c=this.config();if(reset)c.staged=[];if(c.staged?.length&&c.staged.length<3&&c.staged.some(p=>cosine(p,r.embedding)<.4))throw Error('The samples do not sound alike. Record again in a quieter place.');c.staged=[...(c.staged||[]),r.embedding].slice(-3);if(c.staged.length===3)c.profiles=c.staged;this.save(c);this.audit('enrollment_sample',{count:c.staged.length,voicedSeconds:r.voicedSeconds});return this.status();}
 setPassword(value){const clean=normalizePassword(value);if(clean.length<6||clean.length>80||clean.split(' ').length<2)throw Error('Use a short passphrase of at least two words');const c=this.config(),salt=crypto.randomBytes(16).toString('hex');c.password={salt,hash:crypto.scryptSync(clean,salt,32).toString('hex')};this.save(c);return this.status();}
 enable(value){const c=this.config();if(value&&(!(c.profiles?.length>=3)||!c.password))throw Error('Record three voice samples and set a guest passphrase first');c.enabled=!!value;this.save(c);this.attempts.clear();this.grants.clear();this.audit(value?'enabled':'disabled');return this.status();}
 async verify(id,pcm){const a=this.attempt(id);if(++a.checks>3)return {outcome:'guest'};const r=await this.worker.embed(pcm),c=this.config();const scores=c.profiles.map(p=>cosine(p,r.embedding));const score=scores.sort((x,y)=>y-x)[1]||scores[0];const outcome=score>=.65?'owner':score>=.45&&a.checks<2?'uncertain':'guest';this.audit('voice_check',{outcome,score:Number(score.toFixed(3)),voicedSeconds:r.voicedSeconds,inferenceMs:r.inferenceMs});if(outcome==='owner'){this.attempts.delete(id);return {outcome,...this.grant('owner')};}return {outcome};}
 async password(id,pcm){const a=this.attempt(id);if(++a.passwordTries>3)throw Error('Too many attempts; start a new check');const raw=Buffer.from(pcm,'base64');if(raw.length<8000||raw.length>512000)throw Error('Invalid recording');const wav=Buffer.alloc(44+raw.length);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(16000,24);wav.writeUInt32LE(32000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(raw.length,40);raw.copy(wav,44);
  const response=await this.client.audio.transcriptions.create({model:'gpt-4o-mini-transcribe',file:await toFile(wav,'phrase.wav',{type:'audio/wav'}),language:'en'});const c=this.config();if(!c.password)throw Error('Guest passphrase not configured');const hash=crypto.scryptSync(normalizePassword(response.text),c.password.salt,32);const match=crypto.timingSafeEqual(hash,Buffer.from(c.password.hash,'hex'));this.audit('guest_code',{accepted:match});if(match){this.attempts.delete(id);return {outcome:'guest',...this.grant('guest')};}return {outcome:'incorrect',remaining:3-a.passwordTries};
 }
 skip(id){this.attempt(id);this.attempts.delete(id);this.audit('owner_skip');return this.grant('owner');}
}
