import {SidebandWS} from './node_modules/openai/resources/live/sideband/ws.mjs';

// Read-only observation: no audio, transcript text, model instructions, or
// commands are stored or sent. Compare these API events with backend routes.
export class LiveHandoffTrace {
 constructor({client,write,connect=(client,params)=>new SidebandWS(client,params),now=Date.now}){
  Object.assign(this,{client,write,connect,now});this.connection=null;
 }
 emit(row){try{this.write({at:this.now(),...row});}catch{}}
 attach(sessionId,client=this.client){
  this.close();let socket;
  try{socket=this.connect(client,{session_id:sessionId});}catch{this.emit({stage:'observer_failed',sessionId});return;}
  this.connection=socket;
  socket.socket.on('open',()=>this.emit({stage:'observer_connected',sessionId}));
  socket.on('error',()=>this.emit({stage:'observer_error',sessionId}));
  socket.on('event',e=>{
   const allowed=['session.input_transcript.delta','session.output_transcript.delta','session.delegation.created','session.commentary.appended','session.thinking.appended','session.closed','error'];
   if(!allowed.includes(e.type))return;
   const row={stage:e.type,sessionId};
   for(const key of ['start_ms','end_ms','offset_ms'])if(Number.isFinite(e[key]))row[key]=e[key];
   if(typeof e.delta==='string')row.characters=e.delta.length;
   if(e.type==='session.delegation.created'){
    row.target=['client','responses'].includes(e.delegation?.target)?e.delegation.target:'other';
    row.delegationId=e.delegation?.id;
   }
   this.emit(row);
  });
 }
 close(){try{this.connection?.close();}catch{}this.connection=null;}
}
