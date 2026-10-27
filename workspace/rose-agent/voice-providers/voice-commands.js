/* Deterministic short commands; ambiguous language stays with the existing task agent. */
(function(root){
 function resolveVoiceCommand(raw,context={}){
  if(context.appRoutinesEnabled===false)return null;
  const text=String(raw||'').trim().replace(/[.!?]+$/,'').replace(/[“”]/g,'"').replace(/[‘’]/g,"'");
  if(context.messages){
   if(/^(?:(?:please|can you) )?(?:read|check|show me|what are)(?: my| the)? (?:latest|recent)(?: text)? messages$/i.test(text))return {name:'messages_read',params:{operation:'latest'}};
   const reply=text.match(/^(?:any|is there(?: a| any)?|have i (?:had|gotten|received)(?: a| any)?) (?:new )?repl(?:y|ies) from (.+)$/i);
   const read=text.match(/^(?:read|show me|check)(?: my| the)? (?:(?:last|latest|recent) )?(?:text )?messages? (?:from|with) (.+)$/i)||text.match(/^(?:read|show me) (.+?)'s (?:last|latest|recent) messages?$/i);
   const find=text.match(/^(?:find|show me)(?: my| the)? (?:(?:most )?recent )?(?:chat|conversation) with (.+)$/i);
   const match=reply||read||find;
   if(match&&!/^(?:him|her|them|it|that person)$/i.test(match[1].trim()))return {name:'messages_read',params:{operation:reply?'check_reply':read?'read':'find',query:match[1].trim()}};
  }
  if(context.clock){
   const watch=text.match(/^(?:please )?(start|resume|pause|stop|reset|clear)(?: the)? stopwatch$/i);
   if(watch)return {name:'clock_control',params:{operation:'stopwatch_'+({resume:'start',stop:'pause',clear:'reset'}[watch[1].toLowerCase()]||watch[1].toLowerCase())}};
   if(/^(?:record|mark|add)(?: a)? lap$|^lap(?: the stopwatch)?$/i.test(text))return {name:'clock_control',params:{operation:'stopwatch_lap'}};
   if(/^(?:check|read|show)(?: me)?(?: the)? stopwatch$|^how long (?:has|is) the stopwatch (?:been )?running$/i.test(text))return {name:'clock_control',params:{operation:'stopwatch_get'}};
   if(/^(?:please )?(?:(?:stop|silence|dismiss)(?: the| that)? (?:beeping|ringing|timer(?:s)? (?:alarm|beeping|ringing))|(?:silence|dismiss)(?: the| that)? timers?)$/i.test(text))return {name:'clock_control',params:{operation:'timer_silence'}};
   if(/^(?:please )?stop(?: the| that)? timers?$/i.test(text))return {name:'clock_control',params:{operation:'timer_stop'}};
   const timer=text.match(/^(?:please )?(pause|resume|cancel|reset)(?: the)? timer$/i);
   if(timer)return {name:'clock_control',params:{operation:'timer_'+timer[1].toLowerCase()}};
   if(/^(?:how much time is left|check|show|read)(?: on)?(?: the)? timer$/i.test(text))return {name:'clock_control',params:{operation:'timer_get'}};
   if(/^(?:list|show)(?: me)?(?: my| the)? alarms$/i.test(text))return {name:'clock_control',params:{operation:'alarm_list'}};
  }
  const music=text.match(/^(?:(?:please|can you|could you)\s+)?(?:play|put on|start)\s+(.+?)\s+by\s+(.+?)(?:\s+(?:on|in)\s+apple music)?$/i);
  if(music&&!/\b(?:album|playlist)\b/i.test(music[1]))return {name:'apple_music_play',params:{title:music[1].replace(/^"|"$/g,''),artist:music[2].replace(/^"|"$/g,'')}};
  if(context.podcast&&/^(?:keep playing|continue playing|resume(?: the podcast)?|play the podcast)$/i.test(text))return {name:'podcast_control',params:{action:'resume'}};
  if(/^(?:pause|pause (?:it|reading|the book|the podcast))$/i.test(text)){
   if(context.podcast)return {name:'podcast_control',params:{action:'pause'}};
  }
  if(context.media){
   if(/^(?:what(?:'s| is) playing(?: (?:right )?now)?|what (?:song|track)(?: is (?:this|playing)| am i listening to)|(?:tell me )?the current (?:song|track))\??$/i.test(text))return {name:'media_control',params:{operation:'get_state'}};
   if(/^(?:please )?(?:pause|stop)(?: the)? (?:music|song|track)$/i.test(text)||(!context.book&&!context.podcast&&/^(?:please )?pause(?: it)?$/i.test(text)))return {name:'media_control',params:{operation:'pause'}};
   if(/^(?:please )?(?:resume|continue|unpause)(?: the)? (?:music|song|track)$/i.test(text)||/^(?:please )?(?:play|start)(?: the)? music again$/i.test(text)||(!context.book&&!context.podcast&&/^(?:resume|unpause|keep playing|continue playing)$/i.test(text)))return {name:'media_control',params:{operation:'resume'}};
  }
  const song=text.match(/^(?:(?:please|can you|could you)\s+)?(?:play|put on)\s+(?:(?:the\s+)?song\s+)?(.+?)(?:\s+(?:on|in)\s+apple music)?$/i);
  if(song){
   const title=song[1].replace(/^"|"$/g,'').trim();
   const contextual=/^(?:(?:the|a|an)\s+)?(?:it|that|this|something|anything|music|radio|next|again|chess|a game|game|book|podcast|video|movie)\b/i;
   if(title&&!contextual.test(title)&&!/\b(?:album|playlist)\b/i.test(title)&&!/(?:\bby|\bon (?:spotify|youtube))$/i.test(title))return {name:'apple_music_play',params:{title,artist:''}};
  }
  return null;
 }
 root.resolveVoiceCommand=resolveVoiceCommand;
 if(typeof module!=='undefined')module.exports=resolveVoiceCommand;
})(typeof window!=='undefined'?window:globalThis);
