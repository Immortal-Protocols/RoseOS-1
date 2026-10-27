import crypto from 'node:crypto';
import {EventEmitter} from 'node:events';

// One in-memory transfer, no clipboard history, logs or model integration.
export class ClipboardStore extends EventEmitter {
 constructor({timeout=12000}={}){super();this.timeout=timeout;this.current=null;this.last=null;}
 offer(text){
  if(typeof text!=='string'||!text.trim()||Buffer.byteLength(text,'utf8')>65536)throw Error('Copy nonempty text, up to 64 KB');
  this.clear('replaced');
  let resolve;const completion=new Promise(r=>resolve=r);
  const item={id:crypto.randomUUID(),text,expires:Date.now()+this.timeout};
  const timer=setTimeout(()=>this.clear('offline'),this.timeout);timer.unref?.();
  this.current={item,resolve,timer};this.emit('clipboard',item);return {id:item.id,completion};
 }
 pending(){if(this.current&&this.current.item.expires<=Date.now())this.clear('offline');return this.current?.item??null;}
 acknowledge(id,status){
  if(!this.current||id!==this.current.item.id)return false;
  this.clear(status==='copied'?'copied':'failed');return true;
 }
 clear(status='disabled'){
  const c=this.current;if(!c)return;this.current=null;clearTimeout(c.timer);
  this.last={id:c.item.id,status,at:Date.now()};c.item.text='';c.resolve(this.last);
 }
}
