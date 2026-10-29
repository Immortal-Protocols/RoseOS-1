// Registered phone navigation and input for the task worker. A call runs inside its worker
// turn's origin (server.mjs binds appRuntime.handleToolCall), so fastPhone refuses it for a
// stopped, earlier, unknown or missing turn before anything reaches the phone. The commands are
// the existing native actions control.py sent before the shell route became observation-only;
// no new phone action or payload field is introduced, and a caller cannot supply a task.
const snapshot={type:'string',description:'snapshotId from the latest inspect on the same display'};
const node={type:'integer',minimum:0,maximum:299,description:'nodeId from that inspection'};
const label={type:'string',description:'Exact visible text or description of the control'};
const screenshotId={type:'string',description:'screenshotId of a fresh screenshot on the same display'};
const pixels=(min,max)=>({type:'array',items:{type:'integer',minimum:0,maximum:20000},minItems:min,maxItems:max});
const specs=[
 ['phone_inspect_ui','inspect_ui','Read the current screen\'s accessible controls and a fresh snapshotId. Inspect before and after every action.',{}],
 ['phone_open_app','open_app','Open one installed app by exact package name (resolve names with control.py list_apps). Returns opened with a fresh observation when verified; launch_requested is not proof it is visible. Opening does not reset the app.',{packageName:{type:'string',description:'Exact installed package name'}},['packageName']],
 ['phone_home','home','Go to the home screen. In the background workspace this ends the workspace.',{}],
 ['phone_back','back','Android Back.',{}],
 ['phone_tap_label','tap_label','Tap exactly one enabled visible control by its exact label; returns a fresh observation. Preferred for labelled controls.',{snapshotId:snapshot,label,nodeId:{...node,description:'Only to choose between identical labels, from a visually verified target'}},['snapshotId','label']],
 ['phone_touch_label','touch_label','Real touch on a uniquely labelled visible control, only after a verified ineffective tap_label.',{snapshotId:snapshot,label,nodeId:{...node,description:'Only to choose between identical labels'}},['snapshotId','label']],
 ['phone_tap','tap','Tap an unlabelled visible, enabled, clickable node.',{snapshotId:snapshot,nodeId:node},['snapshotId','nodeId']],
 ['phone_long_press','long_press','Long-press a node that exposes long_press.',{snapshotId:snapshot,nodeId:node},['snapshotId','nodeId']],
 ['phone_set_text','set_text','Replace an editable field\'s text. Does not submit.',{snapshotId:snapshot,nodeId:node,text:{type:'string',maxLength:4000}},['snapshotId','nodeId','text']],
 ['phone_ime_enter','ime_enter','Invoke the field\'s keyboard action (Search, Go, Send). It can submit data: only when requested.',{snapshotId:snapshot,nodeId:node},['snapshotId','nodeId']],
 ['phone_scroll','scroll','Scroll an accessible scroll container.',{snapshotId:snapshot,nodeId:node,direction:{type:'string',enum:['forward','backward']}},['snapshotId','nodeId','direction']],
 ['phone_swipe','swipe','Horizontal finger swipe inside a visible node\'s region; left reveals content to the right. Bounds are display pixels from a viewed screenshot.',{snapshotId:snapshot,nodeId:node,direction:{type:'string',enum:['left','right']},bounds:{...pixels(4,4),description:'[left, top, right, bottom]'}},['snapshotId','nodeId','direction','bounds']],
 ['phone_touch_point','touch_point','Tap a screenshot-verified display-pixel point inside a fresh visible node, for a control missing from accessibility.',{snapshotId:snapshot,nodeId:node,x:{type:'integer',minimum:0,maximum:20000},y:{type:'integer',minimum:0,maximum:20000}},['snapshotId','nodeId','x','y']],
 ['phone_drag','drag','One-finger drag between display-pixel endpoints inside a fresh visible node.',{snapshotId:snapshot,nodeId:node,points:{...pixels(4,4),description:'[fromX, fromY, toX, toY]'},durationMs:{type:'integer',minimum:200,maximum:2000}},['snapshotId','nodeId','points']],
 ['phone_screen_gesture','screen_gesture','Tap (one point) or swipe (two points) at display pixels from a fresh screenshot, for a visible control absent from inspection.',{screenshotId,points:pixels(2,4)},['screenshotId','points']],
 ['phone_type_text','type_text','Type into the editor focused in a fresh screenshot; commit submits it.',{screenshotId,text:{type:'string',maxLength:4000},commit:{type:'boolean'}},['screenshotId','text']],
];
const display={type:'string',enum:['background','phone'],description:'background (default) is the agent workspace; phone is the user\'s visible physical screen. Keep IDs and coordinates with the display that produced them.'};
// Successful transport of an action, not proof of its outcome: action_accepted still needs a fresh inspection.
const executed=new Set(['ok','opened','action_accepted','launch_requested']);

export function phoneUiToolDefinitions({background=false}={}){
 return specs.map(([name,,description,properties,required=[]])=>({type:'function',name,description,
  inputSchema:{type:'object',properties:background?{...properties,display}:properties,required,additionalProperties:false}}));
}

function validate(value,schema,key){
 if(schema.type==='string'&&(typeof value!=='string'||schema.maxLength!==undefined&&value.length>schema.maxLength))throw Error('Invalid argument: '+key);
 if(schema.type==='integer'&&(!Number.isInteger(value)||value<schema.minimum||value>schema.maximum))throw Error('Invalid argument: '+key);
 if(schema.type==='boolean'&&typeof value!=='boolean')throw Error('Invalid argument: '+key);
 if(schema.type==='array'){if(!Array.isArray(value)||value.length<schema.minItems||value.length>schema.maxItems)throw Error('Invalid argument: '+key);value.forEach(item=>validate(item,schema.items,key));}
 if(schema.enum&&!schema.enum.includes(value))throw Error('Unsupported argument: '+key);
}

// phone: server.mjs fastPhone, which applies the bound origin, Stop and the display route.
export function createPhoneUiTools({phone,background=()=>false}){
 const routes=new Map(specs.map(([name,action])=>[name,action]));
 async function handleToolCall(request){
  const action=routes.get(request.tool);if(!action)throw Error('Unknown phone tool');
  const definition=phoneUiToolDefinitions({background:background()}).find(t=>t.name===request.tool).inputSchema;
  const args=request.arguments??{};
  if(typeof args!=='object'||Array.isArray(args))throw Error('Arguments must be an object');
  for(const key of definition.required)if(!(key in args))throw Error('Missing argument: '+key);
  for(const [key,value] of Object.entries(args)){
   if(!definition.properties[key])throw Error('Unknown argument: '+key);
   validate(value,definition.properties[key],key);
  }
  const result=await phone({...args,action});
  return {success:executed.has(result?.status)&&result?.actionExecuted!==false,contentItems:[{type:'inputText',text:JSON.stringify(result)}]};
 }
 return {definitions:()=>phoneUiToolDefinitions({background:background()}),handleToolCall,names:new Set(routes.keys())};
}
