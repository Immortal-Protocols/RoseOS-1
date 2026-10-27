// Ordinary app dismissal means leaving its foreground UI. Explicit requests to
// force-stop a process, remove a recent-app card, or close a tab keep their own
// requested result; they are not silently weakened into a Home action.
export function isLeaveAppRequest(request,appLabels=[]){
 const text=request.trim().replace(/^[\s.,!?]+|[\s.,!?]+$/g,'');
 return /^(?:please\s+)?(?:close|exit|leave)\s+.+$/i.test(text)
  && !/\b(?:tab|tabs|window|windows|dialog|popup|pop-up|menu|keyboard|notification|document|account|order|force|process|background|recent|recents|swipe|uninstall)\b/i.test(text)
  && !/\b(?:then|and|after|before)\b/i.test(text)
  && (/\b(?:app|application)\b/i.test(text)||appLabels.some(label=>label&&text.toLowerCase().includes(label.toLowerCase())));
}
export function taskDefinition(request,initialForeground,target){
 const leave=isLeaveAppRequest(request,[target.label,target.packageName]);
 return Object.freeze({request,initialForeground:initialForeground.packageName,
  targetApp:Object.freeze({packageName:target.packageName,label:target.label||target.packageName}),
  completion:Object.freeze({kind:leave?'leave_app':'requested_result',description:leave
   ?'Leave '+(target.label||target.packageName)+' and return Home. Success means the original target is no longer foreground after Home, or was already not foreground. It does not require force-stopping its process or removing its recent-app card. Do not close whichever app appears afterward.'
   :'Complete this request: '+request})});
}
export function observedCompletion(task,screen,history){
 if(task?.completion.kind!=='leave_app'||screen.status!=='ok'||!screen.packageName||screen.packageName===task.targetApp.packageName)return null;
 const alreadyAway=task.initialForeground!==task.targetApp.packageName;
 if(!alreadyAway&&!history.some(h=>h.action==='home'&&h.result==='action_accepted'))return null;
 return {summary:(task.targetApp.label||task.targetApp.packageName)+' is no longer on screen.',evidence:['Original target: '+task.targetApp.packageName,'Current foreground: '+screen.packageName,alreadyAway?'The target was already not foreground.':'Home was accepted and the target is no longer foreground.']};
}

export const taskDefinitionPolicy='The taskDefinition is fixed for this request. targetApp identifies the original app referred to by the request; currentApp/currentScreen only describe the phone now. Navigation never changes that original referent. Evaluate the fixed completion condition before another action. For leave_app, returning Home and observing the original target no longer foreground completes the request; do not escalate to force-stop, remove recent cards, or close the new foreground app. For other tasks, use the stated request, including any explicitly requested multi-app work.';
