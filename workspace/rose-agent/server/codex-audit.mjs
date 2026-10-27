import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {EventEmitter} from 'node:events';
import {readFileSync,writeFileSync} from 'node:fs';
export class Codex extends EventEmitter {
 constructor(root,state,apiKeyFile=null){super();this.root=root;this.state=state;this.apiKeyFile=apiKeyFile;this.authMode=null;this.seq=0;this.pending=new Map();this.active=null;this.thread=null;this.starting=null;}
 async init(){
  this.child=spawn(process.env.PIXEL_CODEX_BIN||'/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex',['app-server','--listen','stdio://',...(this.apiKeyFile?['-c','cli_auth_credentials_store="ephemeral"','-c','model_catalog_json=(process.env.HOME + "/.config/pixel-voice/model-catalog.json")']:[])],{cwd:this.root,stdio:['pipe','pipe','pipe'],env:{...process.env,RUST_LOG:'codex_api=trace,codex_core=trace,feedback_tags=debug'}});
  this.child.stderr.on('data',d=>{const s=d.toString();for(const m of s.matchAll(/service_tier.{0,50}/g))console.log('TIER_AUDIT',m[0]);});
  this.child.on('exit',()=>{for(const p of this.pending.values())p.reject(new Error('Codex process stopped'));this.pending.clear();this.emit('failure','Codex process stopped');});
  createInterface({input:this.child.stdout}).on('line',line=>{let e;try{e=JSON.parse(line)}catch{return}if(e.id!==undefined&&this.pending.has(e.id)){const p=this.pending.get(e.id);this.pending.delete(e.id);clearTimeout(p.timer);e.error?p.reject(new Error(e.error.message)):p.resolve(e.result);return;}if(e.id!==undefined){this.child.stdin.write(JSON.stringify({id:e.id,error:{code:-32000,message:'This prototype cannot approve this request. Ask the user in the main Codex app.'}})+'\n');return;}const p=e.params||{};if(p.threadId&&p.threadId!==this.thread)return;if(e.method==='turn/started'){this.active=p.turn.id;this.emit('status','Codex working');}if(e.method==='item/completed'&&p.item?.type==='agentMessage'){this.emit('message',p.item.text,p.item.phase);}if(e.method==='turn/completed'){this.active=null;this.emit('done',p.turn);} });
  await this.rpc('initialize',{clientInfo:{name:'pixel_voice_poc',title:'Rose Agent',version:'0.1.0'},capabilities:{experimentalApi:true}});
  this.child.stdin.write(JSON.stringify({method:'initialized',params:{}})+'\n');
  if(this.apiKeyFile){
   const apiKey=readFileSync(this.apiKeyFile,'utf8').trim();
   if(!apiKey)throw new Error('Rose Agent coding API key is empty');
   await this.rpc('account/login/start',{type:'apiKey',apiKey});
  }
  const account=await this.rpc('account/read',{});if(!account.account)throw new Error('Codex requires sign in');
  this.authMode=account.account.type;
  if(this.apiKeyFile&&this.authMode!=='apiKey')throw new Error('Rose Agent requires API-key authentication; refusing ChatGPT fallback');
  let saved;try{saved=JSON.parse(readFileSync(this.state,'utf8')).thread}catch{}
  const options={cwd:this.root,approvalPolicy:'never',sandbox:'danger-full-access',developerInstructions:'You are the task backend for a private Pixel voice prototype. User requests arrive as transcripts. Make requested code changes in this workspace and verify results. Respect AGENTS.md. Do not send messages to other people, publish, delete user data, or change credentials without explicit user authorization. Never inspect or print API keys. Do not change the live voice app or server unless specifically asked. Keep progress concise. Return accurate spoken summaries. A correction can arrive during a running turn; incorporate it. Do not spawn subagents. Phone is Pixel serial YOUR_DEVICE_SERIAL; direct ADB tooling only. Reminders project is mono-reminders. Use explicit serial with ADB. For requested Android app builds, publish the final signed APK for wireless delivery by running node rose-agent/delivery/publish.mjs APK_PATH APP_LABEL. This makes it available in Rose Agent. Rose Agent automatically downloads published APKs and, as the device owner, installs them without confirmation. Do not claim installation succeeded from publishing alone. Prefer wireless delivery over ADB installation unless the user asks for USB.'};
  let r;if(saved){try{r=await this.rpc('thread/resume',{...options,threadId:saved})}catch{}}
  r??=await this.rpc('thread/start',options);this.thread=r.thread.id;writeFileSync(this.state,JSON.stringify({thread:this.thread}),{mode:0o600});return this.thread;
 }
 rpc(method,params){return new Promise((resolve,reject)=>{const id=++this.seq;const timer=setTimeout(()=>{this.pending.delete(id);reject(new Error(method+' timed out'));},45000);this.pending.set(id,{resolve,reject,timer});this.child.stdin.write(JSON.stringify({id,method,params})+'\n');});}
 async submit(text,route={model:"gpt-6-astra",effort:"medium"}){const input=[{type:'text',text}];if(this.active){try{return await this.rpc('turn/steer',{threadId:this.thread,expectedTurnId:this.active,input})}catch(e){if(this.active)throw e;}}const r=await this.rpc('turn/start',{threadId:this.thread,input,model:route.model,effort:route.effort,serviceTierForTurn:route.serviceTier??(route.model==='gpt-6-astra'?'ultrafast':'default')});this.active=r.turn.id;return r;}
}
