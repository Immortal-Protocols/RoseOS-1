// Display-only projection of Codex tool events. No calls, retries or task control.
export function activityLabel(item){
 switch(item?.type){
  case 'webSearch':return {openPage:'Reading a web page',findInPage:'Searching a web page'}[item.action?.type]||'Searching the web';
  case 'commandExecution':{
   const actions=item.commandActions||[];
   const kind=actions.length&&actions.every(a=>a.type===actions[0].type)?actions[0].type:'';
   return {read:'Reading files',listFiles:'Listing files',search:'Searching files'}[kind]||'Running a command';
  }
  case 'mcpToolCall':case 'dynamicToolCall':return 'Using an app tool';
  case 'fileChange':return 'Updating files';
  case 'imageView':return 'Viewing an image';
  default:return '';
 }
}

export class ToolActivity {
 constructor(publish){this.publish=publish;this.turnId=null;this.items=new Map();this.last='';}
 show(){
  const text=[...this.items.values()].at(-1)||'';
  if(text===this.last)return;
  this.last=text;this.publish(text);
 }
 event(method,p){
  if(method==='turn/started'){
   this.turnId=p.turn.id;this.items.clear();this.show();return;
  }
  if(!this.turnId||p.turnId&&p.turnId!==this.turnId)return;
  if(method==='turn/completed'){
   if(p.turn.id!==this.turnId)return;
   this.turnId=null;this.items.clear();this.show();return;
  }
  if(method==='item/started'){
   const label=activityLabel(p.item);if(label&&p.item.id)this.items.set(p.item.id,label);
  }else if(method==='item/completed')this.items.delete(p.item?.id);
  else if(method==='item/mcpToolCall/progress'&&this.items.has(p.itemId)){
   const message=String(p.message||'').trim().replace(/\s+/g,' ').slice(0,300);
   if(message)this.items.set(p.itemId,message);
  }else return;
  this.show();
 }
}
