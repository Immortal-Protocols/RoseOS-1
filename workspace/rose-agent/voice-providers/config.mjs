import fs from 'node:fs';
export class VoiceProviderConfig {
 constructor(file){this.file=file;}
 read(){try{const d=JSON.parse(fs.readFileSync(this.file,'utf8'));if(['openai','elevenlabs'].includes(d.provider))return {provider:d.provider};}catch{}return {provider:'elevenlabs'};}
 set(provider){if(!['openai','elevenlabs'].includes(provider))throw Error('Unsupported voice provider');fs.writeFileSync(this.file+'.tmp',JSON.stringify({provider}),{mode:0o600});fs.renameSync(this.file+'.tmp',this.file);return this.read();}
}
