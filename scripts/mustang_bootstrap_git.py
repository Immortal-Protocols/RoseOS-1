"""Bounded local Git reads and raw object verification for source-proof candidates.

No source index, filter, hook, transport, checkout or source program is invoked.
Git's tree listing is reconstructed and hashed back to the pinned commit.
Attribute queries use private metadata containing only authenticated attribute
blobs, never the source repository's config, info attributes or index.
"""
import hashlib
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import tempfile
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
        maximum=min(self.output_bytes,4 << 20) if operation=='commit' else self.output_bytes
        if operation=='blob':maximum=min(maximum,8 << 20)
        return self._run(argv,maximum)

    def _run(self,argv,maximum,input_bytes=None):
        limit=min(self.deadline,time.monotonic()+self.command_seconds)
        if time.monotonic()>=limit:raise Refused('source-proof-deadline')
        proc=subprocess.Popen(argv,env=environment(),stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL,start_new_session=True)
        chunks=[];used=0
        try:
            if input_bytes is not None:proc.stdin.write(input_bytes);proc.stdin.close()
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
            if proc.stdin is not None and not proc.stdin.closed:proc.stdin.close()

    def check_head(self,project,revision):
        if self.read(project,'head').strip()!=oid(revision).encode():raise Refused('project-head-differs')

    def project(self,project,revision,*,max_entries,allow_gitlinks=False):
        if self.read(project,'root').removesuffix(b'\n')!=os.fsencode(project):raise Refused('project-root-differs')
        if self.read(project,'format').strip()!=b'sha1':raise Refused('git-object-format-differs')
        self.check_head(project,revision)
        return tree_rows(self.read(project,'commit',revision),self.read(project,'tree',revision),revision,
                         max_entries=max_entries,include_trees=True,allow_gitlinks=allow_gitlinks)


class PinnedAttributes:
    """Evaluate pinned hierarchy/macros with Git, without source metadata access.

    Scratch directories are private, exclusive and owned by the caller's exit
    stack. Only fixed HEAD/config and hash-verified .gitattributes are written.
    No source file is written and no Git checkout/filter command is executed.
    """
    NAMES=('text','eol','filter','ident','working-tree-encoding','crlf')
    LIMITS=dict(projects=1024,files=4096,bytes=16 << 20,directories=8192)

    def __init__(self,reader,stack):
        self.reader=reader;self.stack=stack;self.projects={};self.charged={key:0 for key in self.LIMITS}

    def charge(self,key,amount):
        self.charged[key]+=amount
        if self.charged[key]>self.LIMITS[key]:raise Refused('pinned-attribute-scratch-'+key+'-bound')

    def query(self,project,name):
        name=relative(name);encoded=os.fsencode(name)
        if len(encoded)>4096:raise Refused('attribute-path-bound')
        key=project['path']
        if key not in self.projects:
            head=b'ref: refs/heads/probe\n';config=b'[core]\nrepositoryformatversion = 0\nbare = false\n'
            for category,amount in (('projects',1),('directories',5),('files',2),('bytes',len(head)+len(config))):
                self.charge(category,amount)
            home=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='mustang-pinned-attributes-')))
            metadata=home/'git';tree=home/'tree';metadata.mkdir(mode=0o700);tree.mkdir(mode=0o700)
            (metadata/'objects').mkdir();(metadata/'refs').mkdir()
            (metadata/'HEAD').write_bytes(head)
            (metadata/'config').write_bytes(config)
            self.projects[key]=dict(metadata=metadata,tree=tree,loaded={})
        scratch=self.projects[key];bindings=[]
        for parent in reversed(Path(name).parents):
            attribute=str(parent/'.gitattributes');row=project['attributes'].get(attribute)
            if row is None:continue
            if row['mode'] not in ('100644','100755'):raise Refused('pinned-attribute-not-regular')
            bindings.append(dict(path=attribute,oid=row['oid']))
            if attribute not in scratch['loaded']:
                raw=self.reader.read(key,'blob',row['oid'])
                if len(raw)>1 << 20:raise Refused('pinned-attribute-byte-bound')
                if object_id('blob',raw)!=row['oid']:raise Refused('pinned-attribute-blob-differs')
                self.charge('files',1);self.charge('bytes',len(raw))
                directory=scratch['tree']
                for component in Path(attribute).parts[:-1]:
                    directory=directory/component
                    if not directory.exists():self.charge('directories',1);directory.mkdir(mode=0o700)
                target=scratch['tree']/attribute
                with target.open('xb') as stream:stream.write(raw)
                scratch['loaded'][attribute]=row['oid']
        argv=['/usr/bin/git','--no-pager','-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null',
              '-c','protocol.allow=never','-c','core.attributesFile=/dev/null','-c','core.autocrlf=false',
              '-c','core.eol=lf','-c','core.ignorecase=false','--git-dir='+str(scratch['metadata']),
              '--work-tree='+str(scratch['tree']),'check-attr','--stdin','-z',*self.NAMES]
        raw=self.reader._run(argv,min(self.reader.output_bytes,128 << 10),encoded+b'\0')
        fields=raw.split(b'\0')
        if len(fields)!=len(self.NAMES)*3+1 or fields[-1]!=b'':raise Refused('pinned-attribute-response-differs')
        result={}
        for i,attribute in enumerate(self.NAMES):
            path,key,value=fields[i*3:i*3+3]
            if path!=encoded or key!=attribute.encode():raise Refused('pinned-attribute-response-differs')
            result[attribute]=value.decode('utf-8','surrogateescape')
        return result,bindings
