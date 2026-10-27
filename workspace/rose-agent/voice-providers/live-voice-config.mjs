import fs from 'node:fs';

// Separate from provider selection: no Android rebuild is needed to try a voice.
export class LiveVoiceConfig {
 constructor(file){this.file=file;}
 read(){
  if(!fs.existsSync(this.file))return {voice:'vesper',name:'Vesper',instructions:''};
  const profile=JSON.parse(fs.readFileSync(this.file,'utf8'));
  if(typeof profile.id!=='string'||!/^voice_[A-Za-z0-9_-]{1,200}$/.test(profile.id))throw Error('Invalid saved Live voice');
  if(typeof profile.prompt!=='string'||!profile.prompt.trim()||profile.prompt.length>2000)throw Error('Invalid saved Live voice description');
  if(profile.languageStyle!==undefined&&(typeof profile.languageStyle!=='string'||profile.languageStyle.length>2000))throw Error('Invalid saved Live speaking style');
  if(profile.project!==undefined&&(typeof profile.project!=='string'||!/^proj_[A-Za-z0-9_-]{1,200}$/.test(profile.project)))throw Error('Invalid saved Live project');
  if(profile.sessionPrompt!==undefined&&(typeof profile.sessionPrompt!=='string'||!profile.sessionPrompt.trim()||profile.sessionPrompt.length>2000))throw Error('Invalid saved Live session prompt');
  return {voice:{id:profile.id},project:profile.project,name:String(profile.name||'Custom voice').slice(0,100),instructions:profile.sessionPrompt??('Voice design: '+profile.prompt+'\nSpeaking style: '+(profile.languageStyle||''))};
 }
}
