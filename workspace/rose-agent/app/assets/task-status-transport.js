(function(root){
 class TaskStatusTransport {
  constructor({state,stream}){this.state=state;this.stream=stream;this.connected=false;}
  receive(event){
   if(event.type==='transport'){this.connected=event.connected===true;this.stream(this.connected);}
   else if((event.type==='hello'||event.type==='task_status')&&event.taskStatus)this.state(event.taskStatus);
  }
  polling(snapshot){if(!this.connected)this.state(snapshot);}
  failed(){if(!this.connected)this.stream(false);}
 }
 if(typeof module!=='undefined'&&module.exports)module.exports=TaskStatusTransport;
 else root.TaskStatusTransport=TaskStatusTransport;
})(typeof window!=='undefined'?window:globalThis);
