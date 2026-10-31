"""SystemUI identity receipt (OS-COMPONENT-ID-002): a pure, fail-closed parser over tool output the caller already has.

Nothing here runs a tool, opens an APK, reads a key or touches the network. The caller (Infra's pipeline) runs
`aapt dump xmltree <apk> AndroidManifest.xml` and `apksigner verify --print-certs <apk>` on the bytes it hashed, and
passes each result as a ToolOutput: exit status, stdout, and the sha256 of the APK bytes that tool read. build_receipt
then returns one deterministic receipt, or raises IdentityError listing every reason it cannot.

Recorded identity:
  * package, numeric versionCode and versionName, from the compiled manifest root;
  * sharedUserId (manifest root) and persistent (application element);
  * the signing certificate's SHA-256, from exactly one apksigner signer;
  * the APK path and sha256, the artifact it came from and the source revision it was built from.

The compiled manifest is read with xmltree, not badging: badging resolves resources against the framework and exits
nonzero for platform APKs (INFRA-ALPHA-116), whereas the identity attributes are literal values in the manifest.
Each optional attribute is reported as {"state": "present", "value": ...} or {"state": "absent"}; "absent" means the
manifest was read and does not carry it, never that extraction failed. Extraction failure, ambiguity (repeated
attributes, several roots or applications, several signers, an unresolved resource reference) or a conflict
(an output for different bytes, another package) is an error, never a partial receipt.

No expected certificate is pinned here: the receipt states the observed certificate only.
"""
import json
import re
from typing import NamedTuple

SCHEMA = 'rose-systemui-identity/1'
SHA256 = re.compile(r'[0-9a-f]{64}')
REVISION = re.compile(r'[0-9a-f]{40}')
PACKAGE = re.compile(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+')
ARTIFACT_KINDS = ('target-files', 'system_ext-image')
# android.R.attr ids; when aapt prints an id it must be this one.
ATTRIBUTE_IDS = {'android:versionCode': '0x0101021b', 'android:versionName': '0x0101021c',
    'android:sharedUserId': '0x0101000b', 'android:persistent': '0x0101000d'}

class IdentityError(ValueError):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__('; '.join(self.problems))

class ToolOutput(NamedTuple):
    returncode: int
    stdout: str
    apk_sha256: str  # sha256 of the APK bytes this tool read, so an output for other bytes is caught

ATTRIBUTE = re.compile(r'A: (?P<name>[\w:]+)(?:\((?P<id>0x[0-9a-f]{8})\))?=(?P<value>.*)')
STRING = re.compile(r'"([^"]*)"(?: \(Raw: "([^"]*)"\))?')
TYPED = re.compile(r'\(type (0x[0-9a-f]+)\)(0x[0-9a-f]+)')
REFERENCE = re.compile(r'@0x[0-9a-f]{8}')

def _value(name, raw, problems):
    """('string'|'int'|'bool', value), or None after recording why the literal cannot be used."""
    string = STRING.fullmatch(raw)
    if string:
        if string[2] is not None and string[2] != string[1]:
            problems.append(f'{name} value and Raw differ')
            return None
        return 'string', string[1]
    typed = TYPED.fullmatch(raw)
    if typed:
        kind, number = int(typed[1], 16), int(typed[2], 16)
        if kind in (0x10, 0x11):  # TYPE_INT_DEC, TYPE_INT_HEX
            return 'int', number
        if kind == 0x12 and number in (0, 0xffffffff):  # TYPE_INT_BOOLEAN
            return 'bool', number != 0
        problems.append(f'{name} has unsupported typed value {raw}')
        return None
    if REFERENCE.fullmatch(raw):
        problems.append(f'{name} is an unresolved resource reference {raw}')
        return None
    problems.append(f'{name} has unreadable value {raw!r}')
    return None

def _elements(text, problems):
    """The manifest root's and its application element's attributes as {name: raw value}, read strictly from xmltree
    output: one root, which is <manifest>; one <application> directly under it; attributes only directly under their
    element and before its first child; nothing but namespace lines before the root."""
    stack, attributes = [], {}  # stack of (indent, element id); attributes per element id
    names, parents, children = {}, {}, set()
    root = None
    for line in text.splitlines():
        stripped = line.lstrip(' ')
        if not stripped or stripped.startswith('N: '):
            continue
        indent = len(line) - len(stripped)
        if stripped.startswith('E: '):
            while stack and stack[-1][0] >= indent:
                stack.pop()
            element = len(names)
            names[element] = stripped[3:].split(' ', 1)[0]
            parents[element] = stack[-1][1] if stack else None
            if parents[element] is not None:
                children.add(parents[element])
            elif root is None:
                root = element
            else:
                problems.append('xmltree output has more than one top-level element')
                return None
            attributes[element] = {}
            stack.append((indent, element))
            continue
        if root is None:
            problems.append('xmltree output has content before the manifest element')
            return None
        if not stripped.startswith('A: '):
            continue
        owner = next((e for i, e in reversed(stack) if indent == i + 2), None)
        if owner is None:
            continue  # an attribute line belongs to the nearest open element at indent - 2; anything else is noise
        if owner != stack[-1][1] or owner in children:
            problems.append(f'<{names[owner]}> attribute after a child element')
            return None
        match = ATTRIBUTE.fullmatch(stripped)
        if not match:
            problems.append(f'unreadable <{names[owner]}> attribute line {stripped!r}')
            return None
        name = match['name']
        if name in attributes[owner]:
            problems.append(f'<{names[owner]}> attribute {name} is repeated')
        known = ATTRIBUTE_IDS.get(name)
        if match['id'] and known and match['id'] != known:
            problems.append(f'{name} has attribute id {match["id"]}, not {known}')
        attributes[owner][name] = match['value']
    if root is None:
        problems.append('xmltree output has no manifest element')
        return None
    if names[root] != 'manifest':
        problems.append(f'xmltree root element is <{names[root]}>, not <manifest>')
        return None
    applications = [e for e, n in names.items() if n == 'application' and parents[e] == root]
    if len(applications) != 1:
        problems.append(f'<manifest> has {len(applications)} <application> elements')
        return None
    return {'manifest': attributes[root], 'application': attributes[applications[0]]}

def _optional(element, name, attributes, kind, problems):
    raw = attributes[element].get(name)
    if raw is None:
        return {'state': 'absent'}
    parsed = _value(name, raw, problems)
    if parsed is None:
        return None
    if parsed[0] != kind:
        problems.append(f'{name} is a {parsed[0]}, expected a {kind}')
        return None
    return {'state': 'present', 'value': parsed[1]}

def parse_manifest(output, apk_sha256):
    """(identity, problems) from `aapt dump xmltree <apk> AndroidManifest.xml`."""
    problems = []
    if output.apk_sha256 != apk_sha256:
        return None, [f'xmltree output is for APK {output.apk_sha256}, not {apk_sha256}']
    if output.returncode != 0:
        return None, [f'aapt xmltree exited {output.returncode}; the manifest was not extracted']
    attributes = _elements(output.stdout, problems)
    if attributes is None or problems:
        return None, problems
    identity = {}
    raw_package = attributes['manifest'].get('package')
    package = _value('package', raw_package, problems) if raw_package is not None else None
    if raw_package is None:
        problems.append('manifest has no package attribute')
    elif package and (package[0] != 'string' or not PACKAGE.fullmatch(package[1])):
        problems.append(f'manifest package {package[1]!r} is not a valid package name')
    elif package:
        identity['package'] = package[1]
    raw_code = attributes['manifest'].get('android:versionCode')
    if raw_code is None:
        problems.append('manifest has no android:versionCode')
    else:
        code = _value('android:versionCode', raw_code, problems)
        if code and code[0] != 'int':
            problems.append('android:versionCode is not an integer')
        elif code:
            identity['versionCode'] = code[1]
    for key, element, name, kind in (('versionName', 'manifest', 'android:versionName', 'string'),
            ('sharedUserId', 'manifest', 'android:sharedUserId', 'string'),
            ('persistent', 'application', 'android:persistent', 'bool')):
        value = _optional(element, name, attributes, kind, problems)
        if value is not None:
            identity[key] = value
    return (identity, []) if not problems else (None, problems)

SIGNER = re.compile(r'Signer #(\d+) certificate SHA-256 digest: ([0-9a-f]{64})')
# EXP006-SIGNER-006: newer apksigner prints the single verified signer as "V3.0 Signer: certificate SHA-256 digest:
# <64 hex>" (observed in run 41158887101 for the image SystemUI APK). One whole-line record of either layout is the
# signer; any other digest line (V3.1, lineage, source stamp, a malformed digest) is refused as before.
V3_0_SIGNER = re.compile(r'V3\.0 Signer: certificate SHA-256 digest: ([0-9a-f]{64})')
ANY_DIGEST = re.compile(r'.*certificate SHA-256 digest: .*')

def parse_signer(output, apk_sha256):
    """(certificate sha256, problems) from `apksigner verify --print-certs`: exactly one signer record, either
    `Signer #1 ...` or `V3.0 Signer: ...`, nothing else; two records are refused even when their digests agree."""
    if output.apk_sha256 != apk_sha256:
        return None, [f'apksigner output is for APK {output.apk_sha256}, not {apk_sha256}']
    if output.returncode != 0:
        return None, [f'apksigner exited {output.returncode}; the signature was not verified']
    signers, others = [], []
    for line in output.stdout.splitlines():
        line = line.strip()
        match, v3 = SIGNER.fullmatch(line), V3_0_SIGNER.fullmatch(line)
        if match:
            signers.append((f'#{int(match[1])}', match[2]))
        elif v3:
            signers.append(('V3.0', v3[1]))
        elif ANY_DIGEST.fullmatch(line):
            others.append(line)  # lineage, source stamp or an unrecognised layout: not this receipt's single signer
    problems = [f'unrecognised certificate digest line {line!r}' for line in others]
    if len(signers) != 1 or (signers and signers[0][0] not in ('#1', 'V3.0')):
        problems.append(f'apksigner reports {len(signers)} signers; exactly one (#1 or V3.0) is required')
    return (signers[0][1], []) if not problems else (None, problems)

def build_receipt(*, apk_path, apk_sha256, xmltree, apksigner, artifact, source, tools, expected_package='com.android.systemui'):
    """The identity receipt as a dict; raises IdentityError with every problem found.

    artifact: {"kind": "target-files" | "system_ext-image", "name": str, "sha256": hex}
    source:   {"revision": 40-hex RoseOS-1 commit, "buildId": optional str}
    tools:    {"aapt": {"path": str, "sha256": hex}, "apksigner": {...}}, the binaries that produced the outputs."""
    problems = []
    if not isinstance(apk_path, str) or not apk_path or apk_path.startswith('/') or '..' in apk_path.split('/'):
        problems.append(f'apk path {apk_path!r} is not a relative artifact path')
    if not isinstance(apk_sha256, str) or not SHA256.fullmatch(apk_sha256):
        problems.append('apk sha256 is not 64 lowercase hex digits')
    if not isinstance(artifact, dict) or artifact.get('kind') not in ARTIFACT_KINDS or not artifact.get('name') \
            or not SHA256.fullmatch(str(artifact.get('sha256', ''))):
        problems.append(f'artifact needs kind in {ARTIFACT_KINDS}, a name and a sha256')
    if not isinstance(source, dict) or not REVISION.fullmatch(str(source.get('revision', ''))):
        problems.append('source needs a 40-hex revision')
    elif set(source) - {'revision', 'buildId'}:
        problems.append(f'unexpected source fields {sorted(set(source) - {"revision", "buildId"})}')
    for name in ('aapt', 'apksigner'):
        tool = (tools or {}).get(name) if isinstance(tools, dict) else None
        if not isinstance(tool, dict) or not tool.get('path') or not SHA256.fullmatch(str(tool.get('sha256', ''))):
            problems.append(f'tools.{name} needs a path and sha256')
    if problems:
        raise IdentityError(problems)
    identity, manifest_problems = parse_manifest(xmltree, apk_sha256)
    certificate, signer_problems = parse_signer(apksigner, apk_sha256)
    problems = manifest_problems + signer_problems
    if identity and identity.get('package') != expected_package:
        problems.append(f'package is {identity.get("package")!r}, not {expected_package!r}')
    if problems:
        raise IdentityError(problems)
    return {'schema': SCHEMA, 'apk': {'path': apk_path, 'sha256': apk_sha256},
        'artifact': {'kind': artifact['kind'], 'name': artifact['name'], 'sha256': artifact['sha256']},
        'source': {k: source[k] for k in sorted(source)},
        'tools': {name: {'path': tools[name]['path'], 'sha256': tools[name]['sha256']} for name in ('aapt', 'apksigner')},
        'package': identity['package'], 'versionCode': identity['versionCode'], 'versionName': identity['versionName'],
        'sharedUserId': identity['sharedUserId'], 'persistent': identity['persistent'],
        'signingCertificateSha256': certificate, 'certificatePinned': False}

def serialize(receipt):
    """Canonical bytes: sorted keys, fixed separators, ASCII, one trailing newline."""
    return (json.dumps(receipt, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode()
