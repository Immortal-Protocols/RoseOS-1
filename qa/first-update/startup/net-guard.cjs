// Preload for the QA startup fixture: refuses every non-loopback network connection or DNS lookup
// and records the attempt, so a full server start cannot reach providers or services.
const net=require('node:net'),tls=require('node:tls'),dns=require('node:dns'),fs=require('node:fs');
const LOG=process.env.QA_NET_LOG;
const loopback=h=>!h||h==='localhost'||h==='::1'||/^127\./.test(h)||h==='0.0.0.0'||h==='::';
const note=(kind,host,port)=>{if(LOG)fs.appendFileSync(LOG,JSON.stringify({kind,host,port})+'\n');};
const hostOf=args=>{const a=args[0];if(a&&typeof a==='object')return [a.host??a.hostname,a.port];return [args[1],a];};
const origConnect=net.Socket.prototype.connect;
net.Socket.prototype.connect=function(...args){const [host,port]=hostOf(args);if(typeof args[0]==='object'&&args[0].path)return origConnect.apply(this,args);
 if(!loopback(host)){note('blocked_connect',host,port);const e=Object.assign(new Error('QA fixture: non-loopback connection refused'),{code:'ECONNREFUSED'});process.nextTick(()=>this.destroy(e));return this;}
 return origConnect.apply(this,args);};
const origLookup=dns.lookup;
dns.lookup=function(host,...rest){if(!loopback(host)){note('blocked_dns',host);const cb=rest.find(f=>typeof f==='function');const e=Object.assign(new Error('QA fixture: DNS refused'),{code:'ENOTFOUND'});if(cb)process.nextTick(()=>cb(e));return;}return origLookup.call(this,host,...rest);};
for(const k of ['HTTPS_PROXY','HTTP_PROXY','https_proxy','http_proxy','ALL_PROXY'])delete process.env[k];
void tls;
