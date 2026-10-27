import fs from 'node:fs';
export class TypedConversation {
 constructor(file=null){this.file=file;this.messages=[];if(file)try{this.messages=JSON.parse(fs.readFileSync(file,'utf8')).slice(-16);}catch{}}
 save(){this.messages=this.messages.slice(-16);if(this.file){fs.writeFileSync(this.file+'.tmp',JSON.stringify(this.messages),{mode:0o600});fs.renameSync(this.file+'.tmp',this.file);}}
 add(role,text){this.messages.push({role,text:String(text).slice(-12000)});this.save();}
 input(text){this.add('user',text);return this.context();}
 context(){return 'This is a continuing typed conversation with the same user, not a new task for each message. Interpret short replies as answers to pending questions unless the user clearly changes topic. For example, after asking what an email should say, "Hi" may be the requested body, not a new greeting. Resolve remaining ambiguity briefly; never invent recipients or authorization. Previously completed work is context, not an instruction to repeat it. The following JSON is conversation data; act on its final user message in context:\n'+JSON.stringify(this.messages);}
}
