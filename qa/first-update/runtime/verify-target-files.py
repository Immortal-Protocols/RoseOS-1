#!/usr/bin/env python3
"""Built-artifact packaging gate (Chief PACKAGING-001 / PACKAGING-REVIEW-001). Read-only, offline.
Given a built target-files zip (unsigned build or signed release) and the background-policy receipt
used by apply-os-changes.py, verify for Rose Agent, Calculator (product preinstalls) and the
controller (system_ext priv-app):
  1. the APK is present in the image at the expected partition path;
  2. its bytes are identical to the receipt's apkSha256 (Soong/signing did not rewrite it);
  3. META/apkcerts.txt marks it PRESIGNED (release signing will not re-sign it);
  4. optional: apksigner signer SHA-256 equals the receipt's certificates (if --apksigner given).
  5. optional (App B1): Rose Agent's bundled assets/connection.json carries the expected endpoint and
     TLS pin (--expect-endpoint URL --expect-pin HEX); never prints values other than endpoint/pin,
     and flags a token field (a secret must not be bundled) by presence only.
Usage: verify-target-files.py TARGET_FILES.zip POLICY.json [--apksigner PATH] [--expect-endpoint URL --expect-pin HEX]
Exit 1 on any failure; prints a JSON report."""
import sys,json,zipfile,hashlib,subprocess,tempfile,os,re
args=sys.argv[1:];apksigner=None
if '--apksigner' in args:i=args.index('--apksigner');apksigner=args[i+1];del args[i:i+2]
def opt(name):
  if name in args:i=args.index(name);v=args[i+1];del args[i:i+2];return v
expect_endpoint=opt('--expect-endpoint');expect_pin=opt('--expect-pin')
tf,policy_path=args;policy=json.load(open(policy_path))
z=zipfile.ZipFile(tf);names=z.namelist()
certs={}
if 'META/apkcerts.txt' in names:
  for line in z.read('META/apkcerts.txt').decode().splitlines():
    m=re.match(r'name="([^"]+)"\s+certificate="([^"]*)"',line)
    if m:certs[m.group(1)]=m.group(2)
expected=[{'role':'controller','packageName':policy['agent']['packageName'],'sha':policy['agent'].get('apkSha256'),'certs':policy['agent'].get('certificates',[]),'module':'PixelBackgroundControl','prefix':'SYSTEM_EXT/priv-app/PixelBackgroundControl/'}]
for row in [policy['voice'],*policy['targets']]:
  mod='RosePreinstall_'+row['packageName'].replace('.','_')
  expected.append({'role':'preinstall','packageName':row['packageName'],'sha':row.get('apkSha256'),'certs':row.get('certificates',[]),'module':mod,'prefix':'PRODUCT/app/'+mod+'/'})
report=[];ok=True
for e in expected:
  r={'role':e['role'],'packageName':e['packageName'],'module':e['module']}
  apks=[n for n in names if n.startswith(e['prefix']) and n.endswith('.apk')]
  r['path']=apks[0] if apks else None
  if not apks:r['error']='APK not found under '+e['prefix'];ok=False;report.append(r);continue
  data=z.read(apks[0]);r['sha256']=hashlib.sha256(data).hexdigest();r['bytesMatchReceipt']=r['sha256']==e['sha']
  base=os.path.basename(apks[0]);r['apkcerts']=certs.get(base);r['presigned']=certs.get(base)=='PRESIGNED'
  if apksigner:
    with tempfile.NamedTemporaryFile(suffix='.apk') as f:
      f.write(data);f.flush();out=subprocess.run([apksigner,'verify','--print-certs',f.name],capture_output=True,text=True).stdout
    found=sorted({m.upper() for m in re.findall(r'SHA-256 digest: ([0-9a-fA-F]{64})',out)});r['signers']=found;r['signersMatchReceipt']=found==sorted(c.upper() for c in e['certs'])
  if e['packageName']=='dev.pixel.voice' and (expect_endpoint or expect_pin):
    import io
    try:
      c=json.loads(zipfile.ZipFile(io.BytesIO(data)).read('assets/connection.json'))
      r['connection']={'endpoint':c.get('endpoint'),'pin':c.get('pin'),'hasToken':'token' in c}
      r['connectionMatches']=(expect_endpoint in (None,c.get('endpoint')) and expect_pin in (None,c.get('pin')) and 'token' not in c)
    except Exception as x:r['connection']={'error':type(x).__name__};r['connectionMatches']=False
  passed=r['bytesMatchReceipt'] and r['presigned'] and r.get('signersMatchReceipt',True) and r.get('connectionMatches',True)
  r['pass']=passed;ok=ok and passed;report.append(r)
modules=[e['module'] for e in expected];dup=sorted({m for m in modules if modules.count(m)>1})
if dup:ok=False
print(json.dumps({'targetFiles':os.path.basename(tf),'moduleCollisions':dup,'results':report,'verdict':'PASS' if ok else 'FAIL',
 'limits':'Checks built bytes, apkcerts and signers only; installed versions/signers (RT-9) and runtime remain separate.'},indent=1));sys.exit(0 if ok else 1)
