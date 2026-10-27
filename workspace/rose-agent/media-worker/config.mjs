import fs from 'node:fs';

export class MediaWorkerConfig {
 constructor(file){this.file=file;}
 read(){try{return {enabled:JSON.parse(fs.readFileSync(this.file,'utf8')).enabled===true};}catch{return {enabled:false};}}
 set(enabled){
  if(typeof enabled!=='boolean')throw Error('enabled must be a boolean');
  fs.writeFileSync(this.file+'.tmp',JSON.stringify({enabled}),{mode:0o600});
  fs.renameSync(this.file+'.tmp',this.file);return this.read();
 }
}
