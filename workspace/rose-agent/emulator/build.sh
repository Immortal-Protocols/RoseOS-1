#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
: "${PIXEL_EMULATOR_DIR:?Set private test output directory}"
mkdir -p "$PIXEL_EMULATOR_DIR"
chmod 700 "$PIXEL_EMULATOR_DIR"
python3 - "$ROOT" "$PIXEL_EMULATOR_DIR" <<'PY'
import sys,pathlib,shutil,json,secrets,os
root,dest=map(pathlib.Path,sys.argv[1:]);app=dest/'app'
app.mkdir(exist_ok=True,mode=0o700)
for name in ['src','res','assets']:
 shutil.copytree(root/'rose-agent/app'/name,app/name,dirs_exist_ok=True)
shutil.copy2(root/'rose-agent/app/AndroidManifest.xml',app/'AndroidManifest.xml')

shutil.copytree(root/'rose-agent/emulator/src',app/'src',dirs_exist_ok=True)
manifest=app/'AndroidManifest.xml'
manifest.write_text(manifest.read_text().replace('</application>', '<receiver android:name=".WavePreviewReceiver" android:exported="true" /></application>'))
service=app/'src/dev/pixel/voice/AgentTrayService.java'
service.write_text(service.read_text().replace('private Bundle state(){', 'private Bundle state(){ Bundle preview=WavePreviewReceiver.state();if(preview!=null)return preview;'))

if os.environ.get('PIXEL_EMULATOR_FRONTEND') == 'production':
 manifest.write_text(manifest.read_text().replace('</application>', '<receiver android:name=".AudioBridgeProbeReceiver" android:exported="true" /></application>'))
else:
 shutil.copy2(root/'rose-agent/emulator/assets/index.html',app/'assets/index.html')
tokenfile=dest/'pair-token'
if not tokenfile.exists():tokenfile.write_text(secrets.token_urlsafe(32))
os.chmod(tokenfile,0o600)
c=json.loads((app/'assets/connection.json').read_text());c['endpoint']='https://127.0.0.1:19444';c['token']=tokenfile.read_text().strip()
(app/'assets/connection.json').write_text(json.dumps(c));os.chmod(app/'assets/connection.json',0o600)
PY
export JAVA_HOME="$ROOT/tools/jdk/Contents/Home"
export PATH="$JAVA_HOME/bin:$PATH"
SDK="$ROOT/tools/android-sdk"; BT="$SDK/android-15"; APP="$PIXEL_EMULATOR_DIR/app"
mkdir -p "$APP/classes"
"$BT/aapt" package -f -M "$APP/AndroidManifest.xml" -S "$APP/res" -A "$APP/assets" -I "$SDK/android-35/android.jar" -F "$APP/unsigned.apk" -J "$APP/src"
javac -source 8 -target 8 -Xlint:-options -bootclasspath "$SDK/android-35/android.jar:$BT/core-lambda-stubs.jar" -d "$APP/classes" "$APP"/src/dev/pixel/voice/*.java "$APP/src/R.java"
"$BT/d8" --lib "$SDK/android-35/android.jar" --output "$APP" "$APP"/classes/dev/pixel/voice/*.class
(cd "$APP" && zip -q unsigned.apk classes.dex)
"$BT/zipalign" -f 4 "$APP/unsigned.apk" "$APP/aligned.apk"
"$BT/apksigner" sign --ks "$ROOT/mono-reminders/development.keystore" --ks-pass pass:android --out "$PIXEL_EMULATOR_DIR/pixel-voice-emulator.apk" "$APP/aligned.apk"
"$BT/apksigner" verify "$PIXEL_EMULATOR_DIR/pixel-voice-emulator.apk"
chmod -R go-rwx "$PIXEL_EMULATOR_DIR"
echo 'Emulator-only APK verified; production app assets and APK untouched.'
