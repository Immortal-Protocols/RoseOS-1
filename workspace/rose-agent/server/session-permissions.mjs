// Permission profiles supplied by the bridge, checked against Codex's effective configuration.
function tomlInline(value) {
 if(typeof value==='string')return JSON.stringify(value);
 if(typeof value==='boolean')return String(value);
 return '{'+Object.entries(value).map(([k,v])=>JSON.stringify(k)+' = '+tomlInline(v)).join(', ')+'}';
}

/** Codex 0.167.1 or later, released: the version whose permission-profile API was checked. Anything else is refused. */
export function supportsSessionPermissionsVersion(value) {
 const m=/^codex-cli (\d+)\.(\d+)\.(\d+)(-[A-Za-z0-9.-]+)?\s*$/.exec(value);
 if(!m||m[4])return false;  // a pre-release of 0.167.1 is not 0.167.1
 const [major,minor,patch]=m.slice(1,4).map(Number);
 return major>0||minor>167||(minor===167&&patch>=1);
}

/** The value with every null-valued key removed, at any depth: Codex's config/read reports each published field of
 * a profile, null when unset (description, workspace_roots, filesystem.glob_scan_max_depth, other network fields), and
 * null is unset. Only nulls go: any other value, extra key or difference still differs. */
export const isObject=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
export function withoutNulls(v){
 if(Array.isArray(v))return v.map(withoutNulls);
 if(!isObject(v))return v;
 return Object.fromEntries(Object.entries(v).filter(([,x])=>x!==null).map(([k,x])=>[k,withoutNulls(x)]));
}

function canonical(v){
 if(v===null||typeof v!=='object')return JSON.stringify(v);
 if(Array.isArray(v))return '['+v.map(canonical).join(',')+']';
 return '{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}';
}

const ISOLATION_KEYS=['sandbox_mode','sandbox_workspace_write','default_permissions','permissions'];

export function sessionPermissions(id, profile, rootsForCwd, errorCode) {
 const refuse=()=>{throw Error(errorCode);};
 return Object.freeze({
  id,
  launchArgs:()=>['--strict-config','-c',`default_permissions=${JSON.stringify(id)}`,
   '-c',`permissions.${id}=${tomlInline(profile)}`],
  roots:rootsForCwd,
  checkConfig(config){
   if(!isObject(config)||config.sandbox_mode!=null||config.sandbox_workspace_write!=null)refuse();
   if(config.profile!=null){
    const selected=typeof config.profile==='string'&&isObject(config.profiles)?config.profiles[config.profile]:undefined;
    if(!isObject(selected)||ISOLATION_KEYS.some(key=>selected[key]!=null))refuse();
   }
   if(config.default_permissions!==id||canonical(withoutNulls(config.permissions?.[id]))!==canonical(profile))refuse();
  },
  checkActive(response,cwd){
   const active=response?.activePermissionProfile;
   if(!isObject(active)||active.id!==id||(active.extends!=null&&active.extends!==profile.extends))refuse();
   const roots=response.runtimeWorkspaceRoots??[],allowed=rootsForCwd(cwd);
   if(!Array.isArray(roots)||roots.some(root=>!allowed.includes(root)))refuse();
  },
  async verifyListed(rpc,cwd){
   let cursor;const seen=new Set();
   for(let page=0;page<16;page++){
    const result=await rpc('permissionProfile/list',{cwd,limit:100,...(cursor?{cursor}:{})});
    if(!Array.isArray(result?.data))refuse();
    const entry=result.data.find(p=>p?.id===id);
    if(entry){if(entry.allowed!==true)refuse();return;}
    if(!result.nextCursor)break;
    cursor=result.nextCursor;
    if(typeof cursor!=='string'||cursor.length>4096||seen.has(cursor))refuse();
    seen.add(cursor);
   }
   refuse();
  },
 });
}

// Voice has no workspace grant. Only the minimal OS runtime is readable by a tool process.
// Explicit temp denies cover the macOS temp aliases even where the minimal runtime permits them.
export const privateSessionFilesystem=Object.freeze({
 ':root':'deny',':minimal':'read',':tmpdir':'deny',':slash_tmp':'deny','/private/var/tmp':'deny',
});
export const voiceSessionProfile=Object.freeze({filesystem:privateSessionFilesystem,network:Object.freeze({enabled:false})});
export const voiceSessionPermissions=sessionPermissions('rose_voice',voiceSessionProfile,()=>[],'voice_isolation_unavailable');
