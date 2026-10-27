#!/usr/bin/env python3
"""Read or select the provider used by the next Rose Agent session."""
import sys,json,importlib.util
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'phone/control.py'
sys.path.insert(0,str(p.parent))
spec=importlib.util.spec_from_file_location('phone_control',p);control=importlib.util.module_from_spec(spec);spec.loader.exec_module(control)
args=sys.argv[1:]
if args and args[0] not in ('openai','elevenlabs'):raise SystemExit('Choose openai or elevenlabs; omit to read current selection.')
print(json.dumps(control.call('/voice/config',{'provider':args[0]}) if args else control.call('/voice/config')))
