// Lossless name/envelope mapping for Codex custom and namespaced tools.
export function prepareRequest(source){
 const body=structuredClone(source),names=new Set(),aliases=new Map(),used=new Set();
 const flatten=(tools,namespace)=>tools.flatMap(t=>{
  if(t.type==='namespace')return flatten(t.tools||[],t.name);
  if(!['function','custom'].includes(t.type))return [t];
  const alias=namespace?namespace+'__'+t.name:t.name;
  if(used.has(alias))throw Error('Ambiguous tool alias');used.add(alias);
  if(namespace)aliases.set(alias,{name:t.name,namespace});
  if(t.type==='custom'){
   names.add(alias);return [{type:'function',name:alias,description:t.description,
    parameters:{type:'object',properties:{input:{type:'string',description:'Exact freeform tool input, unchanged.'}},required:['input'],additionalProperties:false}}];
  }
  return [{...t,name:alias}];
 });
 body.tools=flatten(body.tools||[]);
 body.input=(body.input||[]).map(item=>{
  const alias=item.namespace?item.namespace+'__'+item.name:item.name;
  if(item.type==='custom_tool_call')return {type:'function_call',call_id:item.call_id,name:alias,arguments:JSON.stringify({input:item.input})};
  if(item.type==='function_call'){const {namespace,...rest}=item;return {...rest,name:alias};}
  if(item.type==='custom_tool_call_output'){const {type,...rest}=item;return {type:'function_call_output',...rest};}
  return item;
 });
 delete body.parallel_tool_calls;return {body,names,aliases};
}
function unpack(text){const value=JSON.parse(text);if(typeof value.input!=='string')throw Error('Translated tool requires exact string input');return value.input;}
export function streamAdapter(names,aliases=new Map()){
 const customIds=new Set();let sequence=0;
 const item=source=>{
  if(source?.type!=='function_call')return source;
  let result=source;
  if(names.has(source.name)){
   if(source.id)customIds.add(source.id);
   const {arguments:args,type,...rest}=source;
   result={...rest,type:'custom_tool_call',input:args?unpack(args):''};
  }
  if(aliases.has(source.name))result={...result,...aliases.get(source.name)};
  return result;
 };
 return source=>{
  const e=structuredClone(source);if(e.item)e.item=item(e.item);let events=[e];
  if(customIds.has(e.item_id)){
   if(e.type==='response.function_call_arguments.delta')return [];
   if(e.type==='response.function_call_arguments.done'){
    const input=unpack(e.arguments),{arguments:unused,...rest}=e;
    events=[{...rest,type:'response.custom_tool_call_input.delta',delta:input},{...rest,type:'response.custom_tool_call_input.done',input}];
   }
  }
  if(e.response?.output)e.response.output=e.response.output.map(item);
  for(const event of events)event.sequence_number=sequence++;return events;
 };
}
