/* Shared turn/audio controller. No user-language matching or app-specific routing. */
(function(root){
 function create({dispatch,send,setMuted,level=()=>0,onError=()=>{},now=Date.now,settleMs=1000,drainMs=700}){
  let closed=false,revision=0,timer=null,utterance='',pending=null,lease=false,sounded=false,quietAt=0,deadline=0,lastInput=0;
  const log=(stage,extra={})=>console.log('LUNA_FIRST '+JSON.stringify({stage,revision,...extra}));
  function silence(){lease=false;sounded=false;quietAt=0;setMuted(true);}
  function interrupt(){revision++;pending=null;silence();}
  function input(text){
   interrupt();utterance=text;lastInput=now();clearTimeout(timer);const own=revision;
   function finish(){if(closed||own!==revision)return;if(level()>.015&&now()-lastInput<1800){timer=setTimeout(finish,120);return;}timer=null;log('dispatch');void dispatch(text,own);}
   timer=setTimeout(finish,settleMs);
  }
  function speak(content,begin=()=>{},replyId){
   if(closed)return;silence();pending={content,begin,replyId,revision,started:now()};
   send({type:'session.instructions.append',delegation_id:null,content:'Stop previous speech now. Stay silent until the application supplies the next authorized response.'});
  }
  function tick(inputLevel,outputLevel){
   if(closed)return;
   // Mic energy includes background sound and speaker leakage. Only input()
   // confirms a new utterance and invalidates a reply; Live handles acoustic barge-in.
   if(pending){
    if(outputLevel>.008)quietAt=0;else if(!quietAt)quietAt=now();
    if(quietAt&&now()-quietAt>=drainMs){
     const next=pending;pending=null;if(next.revision!==revision)return;
     lease=true;sounded=false;quietAt=0;deadline=now()+90000;next.begin();
     send({type:next.replyId?'session.commentary.append':'session.instructions.append',delegation_id:next.replyId?.()??null,content:next.content});setMuted(false);log('speech_released');
    }else if(now()-pending.started>6000){pending=null;silence();onError('Voice did not settle; the answer is available on screen.');log('drain_timeout');}
   }else if(lease){
    if(outputLevel>.008){sounded=true;quietAt=0;}else if(sounded){if(!quietAt)quietAt=now();else if(now()-quietAt>1000){silence();log('speech_finished');}}
    if(now()>deadline){silence();log('speech_timeout');}
   }
  }
  silence();
  return {input,speak,tick,interrupt,accepts:own=>!closed&&own===revision,audible:()=>lease,pending:()=>!!pending,close(){closed=true;clearTimeout(timer);pending=null;silence();},state:()=>({revision,lease,pending:!!pending,utterance,lastInput})};
 }
 root.LunaFirst={create};if(typeof module!=='undefined')module.exports=root.LunaFirst;
})(typeof window!=='undefined'?window:globalThis);
