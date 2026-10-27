#!/usr/bin/env python3
"""Create a developer's own local TLS material and public Android connection assets."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
from xml.sax.saxutils import escape
from urllib.parse import urlparse

p = argparse.ArgumentParser()
p.add_argument('--config-dir', required=True, help='Private directory for this developer; never checked into Git')
p.add_argument('--endpoint', default='https://127.0.0.1:9443')
p.add_argument('--installer-cert-sha256', help='Optional own OS PackageInstaller certificate for the legacy fast-install helper')
a = p.parse_args()
u = urlparse(a.endpoint)
if u.scheme != 'https' or not u.hostname or u.username or u.password:
    p.error('Endpoint must be an HTTPS URL without credentials.')
if a.installer_cert_sha256 and (len(a.installer_cert_sha256) != 64 or any(c not in '0123456789abcdefABCDEF' for c in a.installer_cert_sha256)):
    p.error('Installer certificate must be a SHA-256 hex fingerprint.')
private = Path(a.config_dir).expanduser().resolve()
if private.is_relative_to(Path(__file__).resolve().parents[1]):
    p.error('Private backend configuration must be outside this repository.')
os.umask(0o077)
private.mkdir(parents=True, exist_ok=True)
private.chmod(0o700)
key, cert = private/'server-key.pem', private/'server-cert.pem'
if key.exists() != cert.exists():
    p.error('Only one of the TLS key/certificate exists; refusing to replace either.')
if not key.exists():
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp)/'tls.cnf'
        try:
            ipaddress.ip_address(u.hostname)
            endpoint_san = 'IP:' + u.hostname
        except ValueError:
            if any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-' for c in u.hostname):
                p.error('Endpoint hostname is not a valid DNS name.')
            endpoint_san = 'DNS:' + u.hostname
        config.write_text('[req]\ndistinguished_name=dn\nx509_extensions=v3\nprompt=no\n[dn]\nCN=localhost\n[v3]\nsubjectAltName=DNS:localhost,IP:127.0.0.1,'+endpoint_san+'\n')
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','365',
            '-keyout',str(key),'-out',str(cert),'-config',str(config)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    key.chmod(0o600)
der = subprocess.check_output(['openssl','x509','-in',str(cert),'-outform','DER'])
pin = hashlib.sha256(der).hexdigest()
root = Path(__file__).resolve().parents[1]/'workspace'
assets = root/'rose-agent/app/assets'
assets.mkdir(parents=True,exist_ok=True)
for name in ['connection.json','mac-screen-connection.json']:
    (assets/name).write_text(json.dumps({'endpoint':a.endpoint,'pin':pin},indent=2)+'\n')
if a.installer_cert_sha256:
    (assets/'installer-identity.json').write_text(json.dumps({'sha256':a.installer_cert_sha256.lower()},indent=2)+'\n')
(root/'pixel-clipboard/clipboard-connection.json').write_text(json.dumps({'endpoint':a.endpoint},indent=2)+'\n')
raw = root/'rose-agent/app/res/raw'
raw.mkdir(parents=True,exist_ok=True)
(raw/'mac_screen_cert.pem').write_bytes(cert.read_bytes())
network = root/'rose-agent/app/res/xml/mac_screen_network.xml'
network.write_text('<network-security-config><base-config cleartextTrafficPermitted="false"><trust-anchors><certificates src="system"/></trust-anchors></base-config><domain-config cleartextTrafficPermitted="false"><domain includeSubdomains="false">'+escape(u.hostname)+'</domain><trust-anchors><certificates src="system"/><certificates src="@raw/mac_screen_cert"/></trust-anchors></domain-config></network-security-config>\n')
for name in ['pair-token','podcast-recommendation-token']:
    f=private/name
    if not f.exists():
        f.write_text(secrets.token_urlsafe(48)+'\n');f.chmod(0o600)
defaults={'guardian-voice.json':{'port':8797,'token':secrets.token_urlsafe(48)},
    'voice-provider.json':{'provider':'openai'}, 'google-accounts.json':{}}
for name,value in defaults.items():
    f=private/name
    if not f.exists():
        f.write_text(json.dumps(value,indent=2)+'\n');f.chmod(0o600)
print('Created local TLS/configuration and public phone assets. Add your own provider keys before starting the backend. No service was started.')
