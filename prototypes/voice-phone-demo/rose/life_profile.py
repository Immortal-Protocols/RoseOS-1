"""Private life backup and explicit offline writer handoff.

Run with --help. Transfer requires stopping the source backend first; the OS
lease refuses concurrent export. Backup is read-only and may run live. Never
contains pairing credentials, transcripts, TLS material or authentication.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3

from .life_world import WorldStore

def backup(source,destination):
    source,destination=Path(source),Path(destination)
    destination.mkdir(mode=0o700,parents=True,exist_ok=False)
    records={}
    for name in ('diary.sqlite3','notebook/notebook.sqlite3'):
        original=source/name
        if not original.exists():continue
        target=destination/name;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        with sqlite3.connect(f'file:{original}?mode=ro',uri=True) as src,sqlite3.connect(target) as dst:
            src.backup(dst)
        target.chmod(0o600);records[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    identity=source.parent/'identity.json'
    if identity.exists():
        shutil.copyfile(identity,destination/'identity.json');(destination/'identity.json').chmod(0o600)
        records['identity.json']=hashlib.sha256((destination/'identity.json').read_bytes()).hexdigest()
    manifest={'schema':2,'files':records,'transfer':False}
    with sqlite3.connect(f'file:{source/"diary.sqlite3"}?mode=ro',uri=True) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='world_meta'").fetchone():
            manifest['agent_id']=db.execute('SELECT agent_id FROM world_meta').fetchone()[0]
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2));(destination/'manifest.json').chmod(0o600)
    return manifest

def export(source,destination):
    store=WorldStore(source);store.acquire()
    try:
        with store._db() as db:
            db.execute('BEGIN IMMEDIATE');store._fence(db)
            db.execute("UPDATE life_authority SET state='exported',generation=generation+1 WHERE id=1")
            db.execute('UPDATE world_meta SET enabled=0,cancel_epoch=cancel_epoch+1,writer=NULL WHERE id=1')
        result=backup(source,destination);result['transfer']=True
        (Path(destination)/'manifest.json').write_text(json.dumps(result,indent=2))
        return result
    finally:store.release()

def import_profile(bundle,destination,accept_agent_id):
    bundle,destination=Path(bundle),Path(destination)
    manifest=json.loads((bundle/'manifest.json').read_text())
    if not manifest.get('transfer') or manifest.get('agent_id')!=accept_agent_id:
        raise ValueError('Explicit exported agent identity acceptance required')
    if destination.exists():raise ValueError('Import requires an empty destination; preserve existing profiles')
    allowed={'diary.sqlite3','notebook/notebook.sqlite3','identity.json'}
    for name,digest in manifest['files'].items():
        if name not in allowed or (bundle/name).is_symlink() or hashlib.sha256((bundle/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('Transfer integrity check failed')
    destination.mkdir(mode=0o700,parents=True)
    for name in manifest['files']:
        if name=='identity.json':continue
        target=destination/name;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        shutil.copyfile(bundle/name,target);target.chmod(0o600)
    store=WorldStore(destination)
    with store._db() as db:
        if db.execute('SELECT agent_id FROM world_meta').fetchone()[0]!=accept_agent_id:raise ValueError('Identity mismatch')
        db.execute("UPDATE life_authority SET host=?,state='active',generation=generation+1 WHERE id=1",(store.host_id(),))
        db.execute('UPDATE world_meta SET enabled=0,writer=NULL,floor=? WHERE id=1',(store.clock(),))
    # Saved name/voice are included for operator-reviewed restoration; do not
    # overwrite a destination's independent identity or pairing configuration.
    if (bundle/'identity.json').exists():
        shutil.copyfile(bundle/'identity.json',destination/'transferred-identity.json')
        (destination/'transferred-identity.json').chmod(0o600)
    return manifest

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('backup','export','import'))
    p.add_argument('source',type=Path);p.add_argument('destination',type=Path)
    p.add_argument('--accept-agent-id')
    a=p.parse_args()
    result=(import_profile(a.source,a.destination,a.accept_agent_id) if a.action=='import'
            else export(a.source,a.destination) if a.action=='export' else backup(a.source,a.destination))
    print(json.dumps({'schema':result['schema'],'agent_id':result.get('agent_id'),'transfer':result['transfer']}))

if __name__=='__main__':main()
