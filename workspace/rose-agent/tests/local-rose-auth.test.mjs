// Rose remote link v1: LocalRoseAuth (pure Java) on the JVM, plus a cross-check that the phone's
// Java and the Mac's Python (cryptography, prototypes/voice-phone-demo/.venv) agree on the exact
// signed bytes: Python rebuilds the canonical string from the spec on its own and verifies a Java
// signature, and Java verifies a Python signature. No device, network or Keystore.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFileSync, spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const app=fileURLToPath(new URL('../app/',import.meta.url));
const root=path.resolve(app,'../../..');
const python=path.join(root,'prototypes/voice-phone-demo/.venv/bin/python');
const classes=fs.mkdtempSync(path.join(os.tmpdir(),'local-rose-auth-'));
test.after(()=>fs.rmSync(classes,{recursive:true,force:true}));

execFileSync('java',['-m','jdk.compiler/com.sun.tools.javac.Main','-source','8','-target','8','-Xlint:-options','-d',classes,
 path.join(app,'src/dev/pixel/voice/LocalRoseAuth.java'),path.join(app,'tests/LocalRoseAuthTest.java')],{stdio:'inherit'});
const java=(...args)=>spawnSync('java',['-cp',classes,'dev.pixel.voice.LocalRoseAuthTest',...args],{encoding:'utf8'});

const PIN='9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08';
const fields=['GET','/ws/audio','1767225600000','AAECAwQFBgcICQoLDA0ODw',PIN];

test('LocalRoseAuth unit checks pass on the JVM',()=>{
 const r=java();
 assert.equal(r.status,0,r.stdout+r.stderr);
 assert.match(r.stdout,/LocalRoseAuthTest: \d+ checks passed/);
});

const hasVenv=fs.existsSync(python)&&spawnSync(python,['-I','-c','import cryptography'],{encoding:'utf8'}).status===0;
const why='needs prototypes/voice-phone-demo/.venv with cryptography';

// The canonical message exactly as the spec writes it, built independently of the Java code.
const PY_CANONICAL="def canonical(m,p,t,n,pin): return ('rose-auth-v1\\n'+m+'\\n'+p+'\\n'+t+'\\n'+n+'\\n'+pin).encode('utf-8')\n";

test('a signature made in Java verifies in Python over the spec\'s canonical bytes',{skip:hasVenv?false:why},()=>{
 const r=java('--sign',...fields);
 assert.equal(r.status,0,r.stderr);
 const out=Object.fromEntries(r.stdout.trim().split('\n').map(l=>[l.slice(0,l.indexOf('=')),l.slice(l.indexOf('=')+1)]));
 const script=PY_CANONICAL+`
import sys, base64, hashlib
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
spki, device, sig, java_bytes = sys.argv[1], sys.argv[2], sys.argv[3], base64.b64decode(sys.argv[4])
mine = canonical(*sys.argv[5:10])
assert mine == java_bytes, (mine, java_bytes)
der = base64.b64decode(spki)
assert hashlib.sha256(der).hexdigest() == device, 'deviceId is sha256(SPKI DER)'
key = serialization.load_der_public_key(der)
assert isinstance(key.curve, ec.SECP256R1)
key.verify(base64.b64decode(sig, validate=True), mine, ec.ECDSA(hashes.SHA256()))
try:
    key.verify(base64.b64decode(sig), canonical('GET', '/ws/events', *sys.argv[7:10]), ec.ECDSA(hashes.SHA256()))
    raise SystemExit('verified for another path')
except Exception as e:
    if isinstance(e, SystemExit): raise
print('python-verified')
`;
 const py=spawnSync(python,['-I','-c',script,out.spki,out.device,out.signature,out.canonical,...fields],{encoding:'utf8'});
 assert.equal(py.status,0,py.stdout+py.stderr);
 assert.match(py.stdout,/python-verified/);
});

test('a signature made in Python verifies in Java',{skip:hasVenv?false:why},()=>{
 const script=PY_CANONICAL+`
import sys, base64
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
k = ec.generate_private_key(ec.SECP256R1())
spki = k.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
sig = k.sign(canonical(*sys.argv[1:6]), ec.ECDSA(hashes.SHA256()))
print(base64.b64encode(spki).decode()); print(base64.b64encode(sig).decode())
`;
 const py=spawnSync(python,['-I','-c',script,...fields],{encoding:'utf8'});
 assert.equal(py.status,0,py.stderr);
 const [spki,sig]=py.stdout.trim().split('\n');
 assert.equal(java('--verify',spki,...fields,sig).status,0,'Java verifies the Python signature');
 const tampered=[...fields];tampered[3]='AAECAwQFBgcICQoLDA0ODx';
 assert.equal(java('--verify',spki,...tampered,sig).status,1,'and rejects it for another nonce');
});
