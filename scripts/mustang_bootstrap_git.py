"""Bounded local Git reads and raw object verification for source-proof candidates.

No index/worktree command, filter, hook, transport, checkout or source program
is invoked. Git's tree listing is reconstructed and hashed back to the pinned
commit rather than trusting object-file names or index status.
"""
import hashlib
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time


MAX_FAILURE_PATHS=32


def diagnostic_path(value):
    return value if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_+./-]{1,256}',value) \
        and not value.startswith('/') and not any(p in ('','.','..') for p in value.split('/')) else None


class Refused(ValueError):
    def __init__(self,code,*,path=None,paths=()):
        super().__init__(code)
        self.code=code
        self.path=diagnostic_path(path)
        self.paths=[clean for value in (paths[:MAX_FAILURE_PATHS] if isinstance(paths,(tuple,list)) else ())
                    if (clean:=diagnostic_path(value)) is not None]


def relative(value):
    if not isinstance(value,str) or not value or value.startswith('/') or '\x00' in value \
            or any(p in ('','.','..') for p in value.split('/')):
        raise Refused('noncanonical-relative-path')
    return value


def oid(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{40}',value) or value=='0'*40:
        raise Refused('pinned-sha1-required')
    return value


def object_id(kind,data):
    return hashlib.sha1(kind.encode()+b' '+str(len(data)).encode()+b'\0'+data).hexdigest()


def tree_rows(commit,listing,revision,*,max_entries,include_trees=False,allow_gitlinks=False):
    """Verify the commit and every reconstructed tree, then return blob identities."""
    oid(revision)
    if object_id('commit',commit)!=revision or b'\0' in commit or b'\n\n' not in commit:
        raise Refused('commit-object-differs')
    headers=commit.split(b'\n\n',1)[0].split(b'\n')
    if not headers or not headers[0].startswith(b'tree ') or sum(h.startswith(b'tree ') for h in headers)!=1:
        raise Refused('commit-tree-header-differs')
    try:root_tree=oid(headers[0][5:].decode('ascii'))
    except UnicodeError:raise Refused('commit-tree-header-differs') from None
    if listing and not listing.endswith(b'\0'):raise Refused('unterminated-tree-listing')
    records=listing.split(b'\0')[:-1] if listing else []
    if len(records)>max_entries:raise Refused('git-tree-entry-bound-'+str(len(records))+'-max-'+str(max_entries))
    rows={};children={'':[]};trees={'':root_tree}
    for raw in records:
        try:
            fields,path=raw.split(b'\t',1);mode,kind,identity=fields.split(b' ')
            name=relative(os.fsdecode(path));identity=oid(identity.decode('ascii'))
        except (ValueError,UnicodeError):raise Refused('malformed-tree-listing') from None
        if name in rows:raise Refused('duplicate-git-tree-path')
        if (mode,kind) not in ((b'040000',b'tree'),(b'100644',b'blob'),(b'100755',b'blob'),(b'120000',b'blob')) \
                and not (allow_gitlinks and (mode,kind)==(b'160000',b'commit')):
            detail=mode.decode('ascii') if re.fullmatch(rb'[0-7]{6}',mode) else 'invalid'
            raise Refused('unsupported-git-tree-mode-'+detail,path=name)
        parent,_,basename=name.rpartition('/')
        row={'path':name,'mode':mode.decode('ascii'),'oid':identity,'kind':kind.decode('ascii')}
        rows[name]=row;children.setdefault(parent,[]).append((os.fsencode(basename),row))
        if kind==b'tree':trees[name]=identity;children.setdefault(name,[])
    if set(children)-set(trees):raise Refused('git-tree-parent-absent')
    for parent,items in children.items():
        # Git compares a directory basename as though it has a trailing slash.
        ordered=sorted(items,key=lambda pair:pair[0]+(b'/' if pair[1]['kind']=='tree' else b''))
        raw=b''.join(row['mode'].lstrip('0').encode()+b' '+name+b'\0'+bytes.fromhex(row['oid'])
                     for name,row in ordered)
        if object_id('tree',raw)!=trees[parent]:raise Refused('reconstructed-tree-differs')
    return rows if include_trees else {name:row for name,row in rows.items() if row['kind']=='blob'}


def environment():
    return {'PATH':'/usr/bin:/bin','HOME':'/nonexistent','LANG':'C','LC_ALL':'C',
        'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_SYSTEM':'/dev/null','GIT_CONFIG_GLOBAL':'/dev/null',
        'GIT_ATTR_NOSYSTEM':'1','GIT_NO_REPLACE_OBJECTS':'1','GIT_NO_LAZY_FETCH':'1',
        'GIT_ALLOW_PROTOCOL':'','GIT_PROTOCOL_FROM_USER':'0','GIT_OPTIONAL_LOCKS':'0',
        'GIT_TERMINAL_PROMPT':'0'}


class LocalGit:
    """Only fixed metadata/raw-object read forms below reach subprocess.Popen."""
    def __init__(self,deadline,*,output_bytes=128 << 20,total_output_bytes=512 << 20,command_seconds=60):
        self.deadline=deadline;self.output_bytes=output_bytes;self.command_seconds=command_seconds
        self.total_output_bytes=total_output_bytes;self.bytes_read=0
        if any(type(v) is not int or v<1 for v in (output_bytes,total_output_bytes,command_seconds)):
            raise Refused('git-reader-bounds-required')

    def read(self,project,operation,revision=None):
        commands={'root':['rev-parse','--show-toplevel'],
                  'head':['rev-parse','--verify','HEAD'],
                  'format':['rev-parse','--show-object-format']}
        if operation in ('commit','tree','blob'):
            oid(revision)
            arguments=(['ls-tree','-r','-t','-z','--full-tree',revision] if operation=='tree' else
                       ['cat-file',operation,revision])
        elif operation in commands:arguments=commands[operation]
        else:raise Refused('git-read-operation-not-allowed')
        project=Path(project)
        if not project.is_absolute():raise Refused('absolute-project-path-required')
        argv=['/usr/bin/git','--no-pager','-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null',
              '-c','protocol.allow=never','-C',str(project),*arguments]
        limit=min(self.deadline,time.monotonic()+self.command_seconds)
        if time.monotonic()>=limit:raise Refused('source-proof-deadline')
        maximum=min(self.output_bytes,4 << 20) if operation=='commit' else self.output_bytes
        if operation=='blob':maximum=min(maximum,8 << 20)
        proc=subprocess.Popen(argv,env=environment(),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL,start_new_session=True)
        chunks=[];used=0
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout,selectors.EVENT_READ)
                while True:
                    remaining=limit-time.monotonic()
                    if remaining<=0:raise Refused('git-read-deadline')
                    if not selector.select(min(remaining,0.25)):continue
                    block=os.read(proc.stdout.fileno(),65536)
                    if not block:break
                    used+=len(block)
                    self.bytes_read+=len(block)
                    if used>maximum:raise Refused('git-output-bound')
                    if self.bytes_read>self.total_output_bytes:raise Refused('git-total-output-bound')
                    chunks.append(block)
            try:code=proc.wait(timeout=max(0.001,limit-time.monotonic()))
            except subprocess.TimeoutExpired:raise Refused('git-read-deadline') from None
            if code:raise Refused('local-git-read-failed')
            return b''.join(chunks)
        finally:
            try:os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            proc.wait()
            proc.stdout.close()

    def check_head(self,project,revision):
        if self.read(project,'head').strip()!=oid(revision).encode():raise Refused('project-head-differs')

    def project(self,project,revision,*,max_entries,allow_gitlinks=False):
        if self.read(project,'root').removesuffix(b'\n')!=os.fsencode(project):raise Refused('project-root-differs')
        if self.read(project,'format').strip()!=b'sha1':raise Refused('git-object-format-differs')
        self.check_head(project,revision)
        return tree_rows(self.read(project,'commit',revision),self.read(project,'tree',revision),revision,
                         max_entries=max_entries,include_trees=True,allow_gitlinks=allow_gitlinks)
