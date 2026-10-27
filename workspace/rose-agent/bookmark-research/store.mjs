import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const atomic=(file,data)=>{fs.writeFileSync(file+'.tmp',JSON.stringify(data,null,2),{mode:0o600});fs.renameSync(file+'.tmp',file);};
export class BookmarkStore {
 constructor(dir){this.dir=dir;fs.mkdirSync(dir,{recursive:true,mode:0o700});this.file=path.join(dir,'state.json');this.state={enabled:false,jobs:[]};if(fs.existsSync(this.file))this.state=JSON.parse(fs.readFileSync(this.file));
  for(const j of this.state.jobs){if(['researching','researched'].includes(j.status))j.status='queued';if(j.status==='sending')j.status='delivery_uncertain';}this.save();}
 save(){atomic(this.file,this.state);}
 configure(patch){if(typeof patch.enabled==='boolean')this.state.enabled=patch.enabled;if(patch.chatId!==undefined){if(!Number.isSafeInteger(patch.chatId)||patch.chatId<1)throw Error('Invalid recipient chat');this.state.chatId=patch.chatId;}if(patch.recipient!==undefined)this.state.recipient=String(patch.recipient);this.save();}
 accept(data,deviceId){
  if(!this.state.enabled)throw Error('Bookmark research is disabled');
  if(!data||!/^[-a-f0-9]{36}$/i.test(data.eventId||''))throw Error('Invalid bookmark event');
  if(data.packageName!=='com.twitter.android'||!Array.isArray(data.postContext)||!data.postContext.length||data.postContext.length>150)throw Error('Invalid post context');
  if(!Number.isFinite(data.capturedAt)||!Array.isArray(data.triggerBounds)||data.triggerBounds.length!==4||!data.triggerBounds.every(Number.isFinite))throw Error('Invalid capture');
  const prior=this.state.jobs.find(j=>j.eventId===data.eventId&&j.deviceId===deviceId);if(prior)return {id:prior.id,status:prior.status,duplicate:true};
  const postContext=data.postContext.map(x=>({text:String(x.text||'').slice(0,12000),description:String(x.description||'').slice(0,1000)})).filter(x=>x.text||x.description);
  const meaningful=postContext.map(x=>(x.text||x.description).trim()).filter(t=>t&&!/^(?:[\d,.]+[KMB]?|·\s*\d+\s*[smhdw]|Bookmark|Delete Bookmark|Reply|Share|Repost|Like|Impressions|Verified|Image|Video|Unmute|Post options|Explain this post with Grok)$/i.test(t));
  if(!meaningful.length)throw Error('Bookmark identity is missing');
  const fingerprint=crypto.createHash('sha256').update(meaningful.join('\n')).digest('hex');
  const duplicate=this.state.jobs.find(j=>j.fingerprint===fingerprint&&j.deviceId===deviceId);if(duplicate)return {id:duplicate.id,status:duplicate.status,duplicate:true};
  let image=null;if(data.imageBase64){if(typeof data.imageBase64!=='string'||data.imageBase64.length>1100000)throw Error('Capture too large');image=Buffer.from(data.imageBase64,'base64');if(image[0]!==255||image[1]!==216||image.length>800000)throw Error('Expected JPEG');}
  if(this.state.jobs.filter(j=>['queued','researching'].includes(j.status)).length>=25)throw Error('Research queue is full');
  const id=crypto.randomUUID(),dir=path.join(this.dir,id);fs.mkdirSync(dir,{mode:0o700});let imagePath;
  if(image){imagePath=path.join(dir,'capture.jpg');fs.writeFileSync(imagePath,image,{mode:0o600});}
  const job={id,eventId:data.eventId,deviceId,fingerprint,status:'queued',capturedAt:data.capturedAt,receivedAt:Date.now(),triggerBounds:data.triggerBounds,postContext,imagePath,attempts:0};
  this.state.jobs.push(job);this.save();return {id,status:'queued',duplicate:false};
 }
 next(){return this.state.jobs.find(j=>j.status==='queued');}
 update(job,patch){Object.assign(job,patch,{updatedAt:Date.now()});this.save();}
 report(job,report){atomic(path.join(this.dir,job.id,'report.json'),report);fs.writeFileSync(path.join(this.dir,job.id,'report.txt'),report.text,{mode:0o600});}
 status(){return {enabled:this.state.enabled,recipientConfigured:!!this.state.chatId,jobs:this.state.jobs.slice(-30).map(({id,status,receivedAt,updatedAt,error,elapsedMs})=>({id,status,receivedAt,updatedAt,error,elapsedMs}))};}
}
