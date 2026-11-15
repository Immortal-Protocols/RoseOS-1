import subprocess, sys, pathlib
A='rose/app_builder/share.py'
M=[
 ('mapping ignored', "    if record.get('hostedAppId') is None:\n", "    if False:\n"),
 ('404 published-ish', "    if status == 404:\n        return {'schemaVersion': SCHEMA_VERSION, 'status': 'unpublished'}", "    if status == 404:\n        return _unavailable('storage')"),
 ('503 reason swapped', "    if status == 503:\n        return _unavailable('storage')", "    if status == 503:\n        return _unavailable('unreachable')"),
 ('other status ok', "    if status != 200:\n        return _unavailable('unreachable')", "    if False:\n        pass"),
 ('no match check', "    if hosted != expected:", "    if False:"),
 ('size not checked', "    fields = ('app_id', 'package_name', 'version_code', 'signer_sha256', 'sha256', 'size_bytes')", "    fields = ('app_id', 'package_name', 'version_code', 'signer_sha256', 'sha256')"),
 ('links unchecked', "    if release is None or latest is None or any(", "    if any("),
 ('swapped links', "'versionUrl': release, 'latestUrl': latest}", "'versionUrl': latest, 'latestUrl': release}"),
 ('fragment ok', " or parts.fragment or '#' in value:", ":"),
 ('userinfo ok', " or '@' in parts.netloc", ""),
 ('non-ascii ok', "    if any(ord(c) <= 0x20 or ord(c) >= 0x7f for c in value):", "    if False:"),
 ('length ok', " or len(value) > MAX_URL", ""),
 ('cache uncapped', "    return min(int(match.group(1)), MAX_CACHE_S) if match else 0", "    return int(match.group(1)) if match else 0"),
 ('no-store cached', "    if 'no-store' in control or 'no-cache' in control:", "    if False:"),
 ('backoff uncapped', "    return min(int(value), MAX_BACKOFF_S) if value.isdigit() else 60", "    return int(value) if value.isdigit() else 60"),
 ('int type loose', " or type(body['version_code']) is not int", ""),
 ('no answer ok', "    if answer is None or answer.get('status') is None:\n        return _unavailable('unreachable')", "    if answer is None:\n        return _unavailable('unreachable')"),
]
for name, old, new in M:
    p=pathlib.Path(A); s=p.read_text(); assert s.count(old)==1, name
    p.write_text(s.replace(old,new))
    try:
        r=subprocess.run([sys.executable,'-m','unittest','rose.tests.test_app_share'],capture_output=True,text=True,timeout=120); rc=r.returncode
    finally:
        p.write_text(s)
    print(('KILLED ' if rc else 'SURVIVED ')+name)
