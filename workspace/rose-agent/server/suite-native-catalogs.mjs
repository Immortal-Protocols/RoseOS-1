import {createHash} from 'node:crypto';

const name=/^[a-z][a-z0-9_]{0,63}$/;
const pkg=/^(?:[a-zA-Z][\w]*\.)+[\w]+$/;

export function catalogFingerprint(catalog){
 return createHash('sha256').update(JSON.stringify(catalog.tools)).digest('hex');
}

export function mergeNativeCatalogs(current, additions){
 const next={...current,packages:[...(current.packages||[])],catalogs:{...(current.catalogs||{})}};
 const entries=Object.entries(additions);
 for(const [packageName,catalog] of entries){
  if(!pkg.test(packageName)||catalog?.status!=='ok'||!Array.isArray(catalog.tools)||!catalog.tools.length)throw Error('Missing native catalog for '+packageName);
  for(const tool of catalog.tools){
   if(!name.test(tool.name)||!name.test(tool.operation)||typeof tool.description!=='string'||!tool.description.trim()||tool.inputSchema?.type!=='object')throw Error('Invalid native action for '+packageName);
  }
  if(!next.packages.includes(packageName))next.packages.push(packageName);
  next.catalogs[packageName]=catalog;
 }
 const owners=new Map();
 for(const packageName of next.packages){
  const catalog=next.catalogs[packageName];
  if(!catalog?.tools)throw Error('Configured app has no cached catalog: '+packageName);
  for(const tool of catalog.tools){
   if(owners.has(tool.name))throw Error('Duplicate action name: '+tool.name);
   owners.set(tool.name,packageName);
  }
 }
 return next;
}

// Read installed capabilities before changing the live cache. A build artifact
// alone does not establish that the phone has the corresponding provider.
export async function verifyInstalledCatalogs(expected, phone){
 const verified={};
 for(const [packageName,catalog] of Object.entries(expected)){
  const installed=await phone({action:'app_command',packageName,operation:'capabilities'});
  if(installed?.status!=='ok'||!Array.isArray(installed.tools))throw Error('Installed actions unavailable: '+packageName);
  if(catalogFingerprint(installed)!==catalogFingerprint(catalog))throw Error('Installed actions differ from reviewed catalog: '+packageName);
  verified[packageName]=installed;
 }
 return verified;
}
