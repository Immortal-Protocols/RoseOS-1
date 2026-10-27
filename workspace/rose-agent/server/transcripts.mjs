import fs from 'node:fs';
// Local diagnostic history: five nonempty sessions, up to 50,000 characters each.
export class TranscriptHistory {
 constructor(file,record=()=>{}){this.file=file;this.record=record;this.sessions=[];try{const data=JSON.parse(fs.readFileSync(file,'utf8'));if(Array.isArray(data))this.sessions=data.slice(-5);}catch{}this.current=null;}
 start(id){this.current={id,startedAt:new Date().toISOString(),messages:[]};try{this.record('voice.session_started',{sessionId:id});}catch{}}
 append(role,delta){if(!delta||!this.current)return;try{this.record('voice.text',{sessionId:this.current.id,role,text:delta});}catch{}const s=this.current;if(!this.sessions.includes(s)){this.sessions.push(s);this.sessions=this.sessions.slice(-5);}const last=s.messages.at(-1);if(last?.role===role)last.text+=delta;else s.messages.push({role,text:delta,at:new Date().toISOString()});let excess=s.messages.reduce((n,m)=>n+m.text.length,0)-50000;while(excess>0&&s.messages.length){const first=s.messages[0];if(first.text.length<=excess){excess-=first.text.length;s.messages.shift();}else{first.text=first.text.slice(excess);excess=0;}}s.updatedAt=new Date().toISOString();const tmp=this.file+'.tmp';fs.writeFileSync(tmp,JSON.stringify(this.sessions),{mode:0o600});fs.chmodSync(tmp,0o600);fs.renameSync(tmp,this.file);}
 read(){return this.sessions;}
}
