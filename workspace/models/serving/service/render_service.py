#!/usr/bin/env python3
"""Render the persistent Muse Glimmer service tree for the VM (MODELS-ALWAYSON-SERVICE-001). Offline; it runs nothing.

  python3 workspace/models/serving/service/render_service.py --out /etc/rose-models

Output, all self-contained (no repository needed on the VM afterwards):
  bin/            launch-online, launch-offline (render_local.py, profile a100x2-bf16-vllm-persistent),
                  start-model (verifies model storage, then picks one), tls-deploy (certbot deploy hook)
  lib/            the pinned config and the service scripts
  edge/           nginx.conf: TLS, service-auth-v1, and the ACME webroot on :80; acme-only.conf: challenges
                  only on :80, used before the first certificate exists
  systemd/        muse-glimmer, models-health(.timer), models-verify-full(.timer), models-auth, models-edge
  manifest.json   sha256 of every rendered file
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys

SERVING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVING))
import capacity  # noqa: E402
import render_local  # noqa: E402

_spec = importlib.util.spec_from_file_location('render_proxy', SERVING / 'https' / 'render_proxy.py')
render_proxy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(render_proxy)

PROFILE = 'a100x2-bf16-vllm-persistent'
ROOT = '/etc/rose-models'
DATA = '/mnt/models-data'
RUN_EDGE = '/run/rose-edge'
ACME_WEBROOT = '/var/lib/rose-models/acme'
RUN_ACME = '/run/rose-acme'  # the ACME-only Nginx, before the first certificate exists
LIB_FILES = {'lib/muse-glimmer-30b.json': SERVING / 'muse-glimmer-30b.json',
             'lib/https/render_proxy.py': SERVING / 'https' / 'render_proxy.py',
             **{f'lib/service/{n}': SERVING / 'service' / n
                for n in ('verify_model.py', 'health.py', 'auth_service.py', 'tls_deploy.py')}}
PY = '/usr/bin/python3'
NGINX = '/usr/sbin/nginx'


def unit(description, service, unit_extra='', install='WantedBy=multi-user.target'):
    return f'''# Rendered by workspace/models/serving/service/render_service.py. Do not edit by hand.
[Unit]
Description={description}
{unit_extra}
[Service]
{service}
''' + (f'\n[Install]\n{install}\n' if install else '')


def files(config, root=ROOT, data=DATA):
    out = {}
    for offline in (False, True):
        script, _ = render_local.render(config, offline=offline, profile=PROFILE)
        out['bin/launch-' + ('offline' if offline else 'online')] = script
    verify = f'{PY} {root}/lib/service/verify_model.py'
    model_args = f'--cache {data}/hf --receipt {data}/receipt.json'
    out['bin/start-model'] = f'''#!/usr/bin/env bash
# Verify model storage, then start vLLM: offline from the verified cache, or online for the one first download.
set -uo pipefail
{verify} check {model_args}
case $? in
  0) exec {root}/bin/launch-offline ;;
  3) exec {root}/bin/launch-online ;;
  *) echo "refused: model storage does not match the receipt; see verify_model.py" >&2; exit 4 ;;
esac
'''
    nginx = f'{NGINX} -p {RUN_EDGE} -c {root}/edge/nginx.conf'
    out['bin/tls-deploy'] = f'''#!/usr/bin/env bash
exec {PY} {root}/lib/service/tls_deploy.py --tls-dir {root}/tls --pidfile {RUN_EDGE}/nginx.pid \\
  --nginx {NGINX} --nginx-prefix {RUN_EDGE} --nginx-conf {root}/edge/nginx.conf
'''
    for rel, src in LIB_FILES.items():
        out[rel] = src.read_text()
    hardening = ('NoNewPrivileges=yes\nPrivateTmp=yes\nProtectHome=yes\nProtectSystem=strict\n'
                 'RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX')
    out['systemd/muse-glimmer.service'] = unit(
        f'Muse Glimmer vLLM ({PROFILE}) on 127.0.0.1:8000',
        f'''Type=exec
ExecStartPre=-/usr/bin/docker rm -f muse-glimmer
ExecStart={root}/bin/start-model
ExecStopPost=-/usr/bin/docker rm -f muse-glimmer
Restart=on-failure
RestartSec=30
# 4 = a refused precondition (storage mismatch, image missing): restarting cannot fix it.
RestartPreventExitStatus=4
TimeoutStopSec=120''',
        f'After=docker.service network-online.target\nRequires=docker.service\nWants=network-online.target\n'
        f'RequiresMountsFor={data}\nStartLimitIntervalSec=3600\nStartLimitBurst=5')
    health = (f'{PY} {root}/lib/service/health.py run --record {verify} record {model_args}')
    out['systemd/models-health.service'] = unit(
        'Muse Glimmer loaded-model readiness and bounded recovery',
        f'Type=oneshot\nExecStart={health}\nTimeoutStartSec=3700', install=None)
    out['systemd/models-health.timer'] = unit(
        'Run the Muse Glimmer health check every minute', '', install='WantedBy=timers.target').replace(
        '[Service]\n\n', '[Timer]\nOnBootSec=2min\nOnUnitInactiveSec=60s\nAccuracySec=5s\n')
    out['systemd/models-verify-full.service'] = unit(
        'Rehash the Muse Glimmer model cache against its receipt',
        f'Type=oneshot\nExecStart={verify} check --full {model_args}\nNice=10\nIOSchedulingClass=idle', install=None)
    out['systemd/models-verify-full.timer'] = unit(
        'Daily full model verification', '', install='WantedBy=timers.target').replace(
        '[Service]\n\n', '[Timer]\nOnCalendar=daily\nRandomizedDelaySec=1h\nPersistent=yes\n')
    out['systemd/models-auth.service'] = unit(
        'Models service-auth-v1 credential service on 127.0.0.1:8090',
        f'''ExecStart={PY} {root}/lib/service/auth_service.py serve --state-dir /var/lib/rose-models/auth --roots {root}/attestation-roots --revocation-status /var/lib/rose-models/attestation-status.json --self-signed-boot-keys {root}/self-signed-boot-keys.txt
Restart=always
RestartSec=5
ReadWritePaths=/var/lib/rose-models/auth
{hardening}''',
        'After=network.target\nStartLimitIntervalSec=600\nStartLimitBurst=10')
    out['bin/refresh-attestation-status'] = f'''#!/usr/bin/env bash
# Fetch Google's Android attestation status list (revocations) for models-auth; keep the old copy on any failure.
set -euo pipefail
dst=/var/lib/rose-models/attestation-status.json
curl -fsS --proto =https --max-time 60 --max-filesize 16777216 -o "$dst.tmp" https://android.googleapis.com/attestation/status
{PY} -c 'import json, sys; assert isinstance(json.load(open(sys.argv[1]))["entries"], dict)' "$dst.tmp"
mv "$dst.tmp" "$dst"
'''
    out['systemd/models-attestation-status.service'] = unit(
        "Refresh Google's Android attestation status list for models-auth",
        f'Type=oneshot\nExecStart={root}/bin/refresh-attestation-status',
        'After=network-online.target\nWants=network-online.target', install=None)
    out['systemd/models-attestation-status.timer'] = unit(
        'Refresh the attestation status list every 6 hours', '', install='WantedBy=timers.target').replace(
        '[Service]\n\n', '[Timer]\nOnBootSec=1min\nOnUnitActiveSec=6h\nRandomizedDelaySec=10min\nPersistent=yes\n')
    out['systemd/models-edge.service'] = unit(
        'Models Nginx TLS edge for models.a-list.com',
        f'''RuntimeDirectory=rose-edge
RuntimeDirectoryMode=0700
ExecStartPre=/usr/bin/install -d -m 700 {RUN_EDGE}/client_body {RUN_EDGE}/proxy {RUN_EDGE}/fastcgi {RUN_EDGE}/uwsgi {RUN_EDGE}/scgi
ExecStartPre={nginx} -t
ExecStart={nginx} -g 'daemon off;'
ExecReload={nginx} -s reload
Restart=on-failure
RestartSec=5''',
        'After=network-online.target models-auth.service\nWants=network-online.target models-auth.service\n'
        'StartLimitIntervalSec=600\nStartLimitBurst=10')
    return out


def render(out_dir, config_path=capacity.CONFIG, root=ROOT):
    config = capacity.load(config_path)
    out_dir = Path(out_dir)
    edge = render_proxy.render(out_dir / 'edge', 'models.a-list.com', RUN_EDGE, 8080, 8000, 120, front='tls',
                               fullchain=f'{root}/tls/current/fullchain.pem', key=f'{root}/tls/current/privkey.pem',
                               auth='service', auth_port=8090, acme_webroot=ACME_WEBROOT)
    rendered = {}
    for rel, text in files(config, root).items():
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode()
        path.write_bytes(data)
        os.chmod(path, 0o755 if rel.startswith('bin/') else 0o644)
        rendered[rel] = hashlib.sha256(data).hexdigest()
    rendered.update({f'edge/{k}': v for k, v in edge['files'].items()})
    acme = render_proxy.acme_only_conf(RUN_ACME, '0.0.0.0', 80, ACME_WEBROOT).encode()
    (out_dir / 'edge' / 'acme-only.conf').write_bytes(acme)
    rendered['edge/acme-only.conf'] = hashlib.sha256(acme).hexdigest()
    manifest = {'schema': 'rose-models-service/1', 'profile': PROFILE, 'root': root,
                'model': f"{config['model']['repo']}@{config['model']['revision']}",
                'image': render_local.render_upload.profile(config, PROFILE)['runtime']['image_digest'],
                'files': dict(sorted(rendered.items()))}
    (out_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--config', type=Path, default=capacity.CONFIG)
    a = p.parse_args(argv)
    try:
        manifest = render(a.out, a.config)
    except (render_local.render_upload.Rejected, render_proxy.Invalid) as e:
        print(f'rejected: {e}', file=sys.stderr)
        return 2
    print(json.dumps({'out': str(a.out), 'profile': manifest['profile'], 'files': len(manifest['files'])}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
