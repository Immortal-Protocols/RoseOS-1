import fs from 'node:fs';

// An explicit, expiring recovery window distributes ONE already signed,
// credential-free APK. It never exposes older APKs, keys, tasks or user data.
// Used only when a client TLS signing bug prevents the regular installer from
// connecting. Every other HTTP route retains its normal authentication.
export function recoveryDownload(configFile, directory) {
 let config;
 try { config=JSON.parse(fs.readFileSync(configFile,'utf8')); } catch(e) { if(e.code==='ENOENT')return null;throw e; }
 if(config.expiresAt<=Date.now())return null;
 if(!/^[a-f0-9]{64}$/.test(config.id)||config.packageName!=='dev.pixel.voice')throw Error('Invalid recovery release');
 const file=directory+'/'+config.id+'.apk';
 if(!fs.existsSync(file))throw Error('Recovery release missing');
 return {expiresAt:config.expiresAt,handle(req,res,url){
  if(Date.now()>=config.expiresAt||req.method!=='GET')return false;
  if(url.pathname==='/apps'){
   res.writeHead(200,{'content-type':'application/json','cache-control':'no-store'});res.end(JSON.stringify({apps:[config]}));return true;
  }
  if(url.pathname!=='/apps/'+config.id+'/apk')return false;
  res.writeHead(200,{'content-type':'application/vnd.android.package-archive','cache-control':'no-store','content-length':fs.statSync(file).size});
  const stream=fs.createReadStream(file);stream.on('error',()=>res.destroy());res.on('close',()=>stream.destroy());stream.pipe(res);
  res.on('finish',()=>console.log('PAIRING_RECOVERY_APK_SENT',config.id));return true;
 }};
}
