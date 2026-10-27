#!/usr/bin/env python3
"""Render the optional installer helper with the recipient's verified app signer."""
import argparse
from pathlib import Path
import shutil
from background_apks import identity, VOICE

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--voice-apk', type=Path, required=True)
p.add_argument('--sdk', type=int, required=True)
p.add_argument('--aapt', type=Path, required=True)
p.add_argument('--apksigner', type=Path, required=True)
p.add_argument('--out', type=Path, required=True, help='New local output directory')
a = p.parse_args()
voice = identity(a.voice_apk, a.aapt, a.apksigner, a.sdk)
if voice['packageName'] != VOICE or len(voice['certificates']) != 1:
    p.error('Expected a verified, single-signer Rose Agent APK.')
if a.out.exists():
    p.error('Output already exists; choose a new directory.')
source = Path(__file__).resolve().parents[1]/'os/optional-installer'
shutil.copytree(source, a.out)
provider = a.out/'src/com/android/packageinstaller/AgentInstallProvider.kt'
provider.write_text(provider.read_text().replace('__PIXEL_VOICE_SHA256__', voice['certificates'][0].lower()))
print('Prepared the optional installer source for your app signer. No OS source or device was changed.')
