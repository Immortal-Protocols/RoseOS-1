import crypto from 'node:crypto';

// Only content relevant to approval participates. Title/progress updates do not
// invalidate a review, but a new task, correction, answer or card does.
export function taskReviewTarget(task,candidate){
 const html=task?.presentation==='html'&&typeof task.visualization?.html==='string'?task.visualization.html:null;
 const content={taskId:task?.id??null,request:task?.request??'',correction:task?.latestInstruction??'',candidate:String(candidate??''),html};
 const sha256=value=>crypto.createHash('sha256').update(value).digest('hex');
 return {taskId:content.taskId,fingerprint:sha256(JSON.stringify(content)),artifact:html===null?null:{type:'html',html,sha256:sha256(html)}};
}

export function taskReviewMatches(target,task,candidate){
 return target.fingerprint===taskReviewTarget(task,candidate).fingerprint;
}
