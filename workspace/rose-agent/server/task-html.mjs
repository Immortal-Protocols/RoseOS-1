export const taskHtmlTool={type:'function',name:'present_task_visualization',description:'Prepare the current task’s final visual result for the Pixel card. Use only after the substantive work is complete and only in Agent HTML mode. Supply a self-contained, responsive HTML fragment with inline CSS and optional inline JavaScript for local interaction. This displays content only; it cannot execute device actions. Then return the ordinary spoken answer.',inputSchema:{type:'object',properties:{html:{type:'string',description:'Complete visual result. HTML/CSS/SVG; optional local JavaScript. Fits a narrow mobile card. No external resources, network calls, forms, iframes or device controls.'}},required:['html'],additionalProperties:false}};

export function taskHtmlPolicy(mode){return mode==='html'?`Result card mode: Agent HTML. After completing the current task, use present_task_visualization once to create a polished, useful visualization of its result, then give your normal spoken answer. You have the task context: preserve its facts, caveats, uncertainties, and source links. Generate the layout yourself; no second model or separate formatting step. Use self-contained HTML with inline CSS, optional SVG, and optional JavaScript for local tabs/expansion. The card is roughly 320–380 CSS pixels wide. The host provides the Pixel font, cream background, ink colors, and ordinary typography; inherit them and avoid an outer box, duplicate task title, or full-screen app chrome. Choose a layout suited to the result, not a prescribed template. Use clear hierarchy and visually meaningful groupings. Keep text, images, and other visual elements to the minimum needed to make a decision or take action. Omit decoration, repetition, and unnecessary explanation while retaining essential facts, caveats, uncertainties, and source links. Fit the visualization entirely within the visible card, with readable text and no horizontal or vertical scrolling. Shorten and prioritize the content rather than shrinking the text to an unreadable size. Any local tabs must each fit within the card; do not use expansion that makes the card require scrolling. HTTP/HTTPS source links are allowed; external scripts, styles, fonts, images, network calls, frames, forms, and device actions are unavailable. Do not research or invent extra content solely to decorate the card. Present facts as verified only to the extent your task evidence supports them. The HTML is the visible answer; the final spoken answer remains ordinary concise language, not markup.`:'Result card mode: '+mode+'. Do not call present_task_visualization or generate HTML for this task.';}

export function validateTaskHtml(html){
 if(typeof html!=='string'||!html.trim()||html.length>32000)throw Error('HTML must be nonempty and at most 32,000 characters');
 if(/<(?:iframe|frame|object|embed|form|base)\b|<script\b[^>]*\bsrc\s*=|<link\b/i.test(html))throw Error('External resources and embedded apps are unavailable');
 return html;
}

export function createTaskHtmlTools({state,mode,record=()=>{}}){
 const handled=new Map();
 function handleToolCall(request){
  const key=request.callId?request.threadId+':'+request.callId:null;
  if(key&&handled.has(key))return handled.get(key);
  const taskId=state.current?.id;
  const work=Promise.resolve().then(()=>{
   if(mode()!=='html')throw Error('Agent HTML mode is not selected');
   if(request.tool!==taskHtmlTool.name||!state.active())throw Error('No active task for this visual');
   const args=request.arguments;
   if(!args||Object.keys(args).some(k=>k!=='html'))throw Error('Expected html only');
   const html=validateTaskHtml(args.html);
   const result=state.setVisualization(taskId,html);
   record({taskId,...result,htmlLength:html.length});
   return {success:result.status==='prepared',contentItems:[{type:'inputText',text:JSON.stringify(result)}]};
  }).catch(error=>({success:false,contentItems:[{type:'inputText',text:JSON.stringify({status:'not_applied',reason:error.message})}]}));
  if(key)handled.set(key,work);
  while(handled.size>100)handled.delete(handled.keys().next().value);
  return work;
 }
 return {definitions:[taskHtmlTool],handleToolCall};
}
