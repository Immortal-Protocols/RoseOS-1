import {runYouTubeSpeed} from './youtube-visual.mjs';
import {cameraToolOutput} from './camera-tool-output.mjs';
// Definitions come from the installed apps. Selection stays with the model.
export async function loadAppToolCatalog({phone,packages,catalogs,adapters=[],requestContext=()=>({})}){
 const routes=new Map(),definitions=[];
 for(const packageName of packages){
  // A verified installed-app catalog may be cached so a disconnected phone
  // does not prevent the voice backend from starting. Calls still hit the app.
  const catalog=catalogs?.[packageName]??await phone({action:'app_command',packageName,operation:'capabilities'});
  if(catalog.status!=='ok'||!Array.isArray(catalog.tools))throw Error('No tool catalog for '+packageName);
  for(const spec of catalog.tools){
   if(!/^[a-z][a-z0-9_]{0,63}$/.test(spec.name)||routes.has(spec.name))throw Error('Invalid or duplicate app tool name');
   if(!/^[a-z][a-z0-9_]{0,63}$/.test(spec.operation)||typeof spec.description!=='string'||spec.inputSchema?.type!=='object')throw Error('Invalid app tool definition');
   if(spec.foregroundRequired!==undefined&&typeof spec.foregroundRequired!=='boolean')throw Error('Invalid foreground requirement');
   if(spec.launchPackage!==undefined&&(!/^(?:[a-zA-Z][\w]*\.)+[\w]+$/.test(spec.launchPackage)||spec.foregroundRequired===false))throw Error('Invalid launch package');
   routes.set(spec.name,{packageName,launchPackage:spec.launchPackage||packageName,operation:spec.operation,schema:spec.inputSchema,foregroundRequired:spec.foregroundRequired!==false});
   definitions.push({type:'function',name:spec.name,description:spec.description,inputSchema:spec.inputSchema});
  }
 }
 for(const adapter of adapters){
  if(!/^(?:[a-zA-Z][\w]*\.)+[\w]+$/.test(adapter.packageName)||typeof adapter.execute!=='function')throw Error('Invalid local app adapter');
  for(const spec of adapter.tools){
   if(!/^[a-z][a-z0-9_]{0,63}$/.test(spec.name)||routes.has(spec.name)||typeof spec.description!=='string'||spec.inputSchema?.type!=='object')throw Error('Invalid or duplicate local app tool');
   routes.set(spec.name,{packageName:adapter.packageName,launchPackage:adapter.packageName,operation:spec.name,schema:spec.inputSchema,foregroundRequired:spec.foregroundRequired!==false,adapter});
   definitions.push({type:'function',name:spec.name,description:spec.description,inputSchema:spec.inputSchema});
  }
 }
 let queue=Promise.resolve();const calls=new Map();
 async function execute(request){
  const route=routes.get(request.tool);if(!route)throw Error('Unknown registered tool');
  const args=request.arguments;
  if(!args||typeof args!=='object'||Array.isArray(args))throw Error('Arguments must be an object');
  for(const key of route.schema.required||[])if(!(key in args))throw Error('Missing argument: '+key);
  for(const [key,value] of Object.entries(args)){
   const field=route.schema.properties[key];if(!field)throw Error('Unknown argument: '+key);
   if(field.type==='string'&&typeof value!=='string'||field.type==='integer'&&!Number.isInteger(value))throw Error('Invalid argument: '+key);
   if(field.type==='number'&&(typeof value!=='number'||!Number.isFinite(value))||field.type==='boolean'&&typeof value!=='boolean')throw Error('Invalid argument: '+key);
   if(field.enum&&!field.enum.includes(value))throw Error('Unsupported argument: '+key);
   if(field.minimum!==undefined&&value<field.minimum||field.maximum!==undefined&&value>field.maximum)throw Error('Argument outside supported range: '+key);
  }
  const current=route.foregroundRequired&&route.adapter?.reuseForeground?await phone({action:'inspect_ui'}):null;
  const opened=current?.status==='ok'&&current.packageName===route.launchPackage
   ?{status:'opened',packageName:route.launchPackage,...(current.backgroundExecution?{backgroundExecution:true,backgroundVerified:true}:{foregroundVerified:true}),reused:true}
   :route.foregroundRequired?await phone({action:'open_app',packageName:route.launchPackage}):null;
  if(route.foregroundRequired&&(!opened||opened.status!=='opened'||!(opened.foregroundVerified===true||opened.backgroundVerified===true)||opened.packageName!==route.launchPackage))
   return {success:false,contentItems:[{type:'inputText',text:JSON.stringify({status:'not_executed',reason:'App opening was not verified',opening:opened})}]};
  const result=route.adapter?await route.adapter.execute(request.tool,args,{threadId:request.threadId,callId:request.callId,...requestContext()}):route.packageName==='dev.pixel.voice'&&route.operation==='youtube_speed'?await runYouTubeSpeed({phone,args}):await phone({action:'app_command',packageName:route.packageName,operation:route.operation,params:args});
  const placement=opened?.backgroundVerified?{backgroundExecution:true,backgroundVerified:true}:opened?{foregroundVerified:true}:{backgroundExecution:true};
  if(route.packageName==='dev.pixel.aicamera')return cameraToolOutput({app:route.packageName,...result,...placement});
  return {success:['ok','verified'].includes(result.status),contentItems:[{type:'inputText',text:JSON.stringify({app:route.packageName,...result,...placement})}]};
 }
 function handleToolCall(request){
  const key=request.threadId+':'+request.callId;
  if(calls.has(key))return calls.get(key);
  // Tools may arrive concurrently; serialize foreground use across apps.
  const work=queue.catch(()=>{}).then(()=>execute(request));queue=work;calls.set(key,work);
  return work;
 }
 return {definitions,handleToolCall};
}
