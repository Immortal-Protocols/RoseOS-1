#!/usr/bin/env python3
"""Build the portable background agent with an explicit SDK and the recipient's key."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--android-jar', type=Path, required=True)
p.add_argument('--build-tools', type=Path, required=True, help='SDK build-tools directory containing aapt, d8, zipalign, apksigner')
p.add_argument('--out', type=Path, required=True)
p.add_argument('--viewer', choices=['pip', 'split', 'none'], default='pip',
    help='Phone floating viewer (default), Fold split viewer, or original controller')
p.add_argument('--keystore', type=Path, help='Same keystore used for Rose Agent; omit only for an unsigned compile check')
p.add_argument('--alias', help='Key alias, required with --keystore')
p.add_argument('--store-pass-env', default='BACKGROUND_KEYSTORE_PASSWORD')
p.add_argument('--key-pass-env', default='BACKGROUND_KEY_PASSWORD')
a = p.parse_args()
if bool(a.keystore) != bool(a.alias):p.error('--keystore and --alias must be supplied together')
if a.keystore and (a.store_pass_env not in os.environ or a.key_pass_env not in os.environ):
    p.error('Set the named password environment variables; no passwords are accepted on the command line')
bt = a.build_tools.resolve(); sdk = a.android_jar.resolve(strict=True)
source = Path(__file__).resolve().parents[1]/'os/background-control'/({'pip':'agent-pip','split':'agent-split','none':'agent'}[a.viewer])
out = a.out.resolve(); out.mkdir(parents=True, exist_ok=True)
def run(*args):subprocess.run([str(x) for x in args], check=True)
with tempfile.TemporaryDirectory(prefix='background-agent-') as directory:
    tmp = Path(directory); classes = tmp/'classes'; classes.mkdir(); dex = tmp/'dex'; dex.mkdir()
    run(bt/'aapt', 'package', '-f', '-M', source/'AndroidManifest.xml', '-I', sdk, '-F', tmp/'unsigned.apk')
    java = str(Path(os.environ['JAVA_HOME'])/'bin/java') if os.environ.get('JAVA_HOME') else 'java'
    run(java, '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-source', '8', '-target', '8', '-Xlint:-options', '-bootclasspath', os.pathsep.join([str(sdk), str(bt/'core-lambda-stubs.jar')]), '-d', classes, *sorted(source.rglob('*.java')))
    run(bt/'d8', '--lib', sdk, '--output', dex, *sorted(classes.rglob('*.class')))
    import zipfile
    with zipfile.ZipFile(tmp/'unsigned.apk', 'a') as z:z.write(dex/'classes.dex', 'classes.dex', compress_type=zipfile.ZIP_STORED)
    run(bt/'zipalign', '-f', '4', tmp/'unsigned.apk', tmp/'aligned.apk')
    if a.keystore:
        run(bt/'apksigner', 'sign', '--ks', a.keystore.resolve(), '--ks-key-alias', a.alias,
            '--ks-pass', 'env:'+a.store_pass_env, '--key-pass', 'env:'+a.key_pass_env,
            '--v4-signing-enabled', 'true', '--out', out/'Agent.apk', tmp/'aligned.apk')
        run(bt/'apksigner', 'verify', '--print-certs', out/'Agent.apk')
        print(f'Signed agent: {out / "Agent.apk"}; retain its .idsig for GrapheneOS app updates.')
    else:
        shutil.copy2(tmp/'aligned.apk', out/'Agent-unsigned.apk')
        print('Compile check passed; unsigned artifact cannot be used as the prebuilt or installed update.')
