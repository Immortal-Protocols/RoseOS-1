"""Inactive source-proof adapter: local metadata and bytes only, with no runtime admission.

The default CLI renders a plan. There is no execution switch or workflow hook.
The library is exercised on synthetic repositories by offline tests. A future
guest caller must separately establish host/disk identity and authority before
calling observe_prepared. This module never starts a VM, fetches, builds, runs a
source program, writes a receipt, allocates, signs or changes the source tree.
"""
from collections import deque
from dataclasses import asdict,dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import time
import xml.etree.ElementTree as ET

import alpha_source
import mustang_bootstrap_git as git
import mustang_existing_lock as locks
import mustang_hostdeps as deps
import mustang_hosttools as readers
import mustang_paths
import mustang_preparation_inputs as inputs
import mustang_vendor_consumer as prepared

ROOT=Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Limits:
    seconds:int=7200
    entries:int=4_000_000
    bytes:int=150 << 30
    git_output_bytes:int=128 << 20
    git_total_output_bytes:int=512 << 20
    git_command_seconds:int=60

    def validate(self):
        if any(type(v) is not int or v<1 for v in asdict(self).values()):
            raise git.Refused('positive-source-proof-bounds-required')
        if any(getattr(self,k)>v for k,v in asdict(Limits()).items()):
            raise git.Refused('source-proof-ceiling-exceeded')
        return self


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':')).encode()+b'\n'


def portable_inventory(rows,source):
    """Copy rebases absolute in-tree links; compare their root-independent text."""
    prefix=str(source).rstrip('/')+'/'
    normalized=[]
    for row in rows:
        item=dict(row)
        if 'link' in item and item['link'].startswith(prefix):item['link']='@source/'+item['link'][len(prefix):]
        normalized.append(item)
    normalized.sort(key=lambda item:item['path'])
    return hashlib.sha256(canonical(normalized)).hexdigest()


def signature(info):
    return tuple(getattr(info,k) for k in ('st_dev','st_ino','st_size','st_mode','st_mtime_ns','st_ctime_ns'))


class Observation:
    def __init__(self,source,limits,*,deadline=None):
        self.source=Path(source);self.limits=limits.validate()
        self.deadline=min(time.monotonic()+limits.seconds,deadline) if deadline is not None else time.monotonic()+limits.seconds
        if readers._body(str(self.source)) is None:raise git.Refused('absolute-source-root-required')
        self.device=deps.plain_dir(self.source).st_dev;self.entries=0;self.bytes=0
        self.excluded=[];self.measured={};self.signatures={}
        self.known_files=None;self.known_directories=set();self.prepared_prefixes=();self.empty_gitlinks=set()

    def check(self):
        if time.monotonic()>=self.deadline:raise git.Refused('source-proof-deadline')

    def count(self):
        self.check();self.entries+=1
        if self.entries>self.limits.entries:raise git.Refused('source-entry-bound')

    def link_text(self,name,parent):
        self.check();text=os.readlink(name,dir_fd=parent)
        self.bytes+=len(os.fsencode(text))
        if self.bytes>self.limits.bytes:raise git.Refused('source-byte-bound')
        return text

    def measure(self,path,expected=None):
        self.check();fd,before=readers._open_regular(str(self.source),str(path))
        try:
            if before.st_dev!=self.device or (expected is not None and signature(before)!=signature(expected)):
                raise git.Refused('selected-file-changed')
            if self.bytes+before.st_size>self.limits.bytes:raise git.Refused('source-byte-bound')
            sha=hashlib.sha256();blob=hashlib.sha1(b'blob '+str(before.st_size).encode()+b'\0');size=0
            while chunk:=os.read(fd,1 << 20):
                self.check();size+=len(chunk);self.bytes+=len(chunk)
                if size>before.st_size or self.bytes>self.limits.bytes:raise git.Refused('source-byte-bound')
                sha.update(chunk);blob.update(chunk)
            if size!=before.st_size or signature(before)!=signature(os.fstat(fd)):
                raise git.Refused('selected-file-changed')
            self.signatures[str(Path(path).relative_to(self.source))]=signature(before)
            return dict(mode=stat.S_IMODE(before.st_mode),sizeBytes=size,sha256=sha.hexdigest(),blob=blob.hexdigest())
        finally:os.close(fd)

    def resolve_link(self,base,relative,text):
        """Resolve components in filesystem order; never follow a link outside base."""
        base=Path(base);kind='absolute' if os.path.isabs(text) else 'relative'
        def components(raw):
            if os.path.isabs(raw):
                if raw==str(base):return []
                prefix=str(base)+'/'
                if not raw.startswith(prefix):raise git.Refused('selected-link-escapes')
                return raw[len(prefix):].split('/')
            return raw.split('/')
        pending=deque(components(text) if kind=='absolute' else [*Path(relative).parent.parts,*components(text)])
        rootfd=readers._walk_dir(readers._body(str(base)));fds=[rootfd];parts=[];hops=0;exists=True;missing=False
        try:
            while pending:
                self.check();part=pending.popleft()
                if part in ('','.'):continue
                if part=='..':
                    if not parts:raise git.Refused('selected-link-escapes')
                    parts.pop();old=fds.pop()
                    if old is not None:os.close(old)
                    exists=fds[-1] is not None;continue
                parent=fds[-1]
                if parent is None:
                    parts.append(part);fds.append(None);exists=False;missing=True;continue
                try:info=os.stat(part,dir_fd=parent,follow_symlinks=False)
                except FileNotFoundError:
                    parts.append(part);fds.append(None);exists=False;missing=True;continue
                if info.st_dev!=self.device:raise git.Refused('selected-link-crosses-filesystem')
                if stat.S_ISLNK(info.st_mode):
                    hops+=1
                    if hops>40:raise git.Refused('selected-link-cycle')
                    target=self.link_text(part,parent)
                    if signature(info)!=signature(os.stat(part,dir_fd=parent,follow_symlinks=False)):
                        raise git.Refused('selected-link-changed')
                    if os.path.isabs(target):
                        nextparts=components(target)
                        while len(fds)>1:
                            old=fds.pop()
                            if old is not None:os.close(old)
                        parts=[]
                    else:nextparts=components(target)
                    pending.extendleft(reversed(nextparts));continue
                parts.append(part);exists=True
                if pending:
                    if not stat.S_ISDIR(info.st_mode):raise git.Refused('selected-link-nondirectory-component')
                    child=os.open(part,readers._DIR_FLAGS,dir_fd=parent)
                    if signature(info)!=signature(os.fstat(child)):
                        os.close(child);raise git.Refused('selected-link-changed')
                    fds.append(child)
                else:fds.append(None)
            return kind,'/'.join(parts) or '.',exists and not missing
        finally:
            for fd in reversed(fds):
                if fd is not None:os.close(fd)

    def plan(self,base,policy):
        """Bounded planner for the unchanged shared source_selection function."""
        base=Path(base);entries=[];excluded=[]
        if policy.get('externalLinks'):raise git.Refused('external-link-exception-not-supported')
        boundaries=policy.get('cacheBoundaries',[])
        for boundary in boundaries:
            self.check();directory=base/boundary['path']
            parent=readers._walk_dir(readers._body(str(directory.parent)))
            try:
                try:info=os.stat(directory.name,dir_fd=parent,follow_symlinks=False)
                except FileNotFoundError:info=None
                if info is not None:
                    if not stat.S_ISDIR(info.st_mode):
                        raise git.Refused('cache-boundary-not-plain-directory',path=boundary['path'])
                    fd=os.open(directory.name,readers._DIR_FLAGS,dir_fd=parent);os.close(fd)
            finally:os.close(parent)
            marker=base/boundary['pruneMarker']
            if readers.read_regular(str(self.source),str(marker),1)!=b'':
                raise git.Refused('cache-prune-marker-differs')
            if alpha_source.inside_excluded(boundary['pruneMarker'],policy):
                raise git.Refused('cache-prune-marker-excluded')
        rootfd=readers._walk_dir(readers._body(str(base)))
        def failed(_error):raise git.Refused('source-walk-incomplete')
        try:
            for directory,dirs,files,parent in os.fwalk('.',topdown=True,dir_fd=rootfd,follow_symlinks=False,onerror=failed):
                dirs.sort();files.sort();self.check()
                prefix='' if directory=='.' else directory[2:];unclassified=[]
                directory_path=base/prefix
                if str(directory_path.relative_to(self.source)) in self.empty_gitlinks and (dirs or files):
                    raise git.Refused('unpinned-gitlink-contents',path=str(directory_path.relative_to(self.source)))
                self.signatures[str(directory_path.relative_to(self.source))]=signature(os.fstat(parent))
                for name in sorted(dirs+files):
                    self.count();rel=prefix+'/'+name if prefix else name
                    reason=alpha_source.excluded_reason(rel,policy)
                    if reason:
                        if name in dirs:dirs.remove(name)
                        item=dict(path=str((base/rel).relative_to(self.source)),reason=reason)
                        self.excluded.append(item);excluded.append(item);continue
                    info=os.stat(name,dir_fd=parent,follow_symlinks=False);mode=stat.S_IMODE(info.st_mode)
                    if info.st_dev!=self.device:raise git.Refused('selected-entry-crosses-filesystem')
                    if mode&0o7000:raise git.Refused('selected-entry-privileged-mode')
                    path=base/rel;full=str(path.relative_to(self.source))
                    if self.known_files is not None:
                        qualified=any(full==p or full.startswith(p+'/') for p in self.prepared_prefixes)
                        if stat.S_ISDIR(info.st_mode):
                            if full not in self.known_directories and not qualified:
                                if len(unclassified)<git.MAX_FAILURE_PATHS:unclassified.append((full,'directory'))
                                if name in dirs:dirs.remove(name)
                                continue
                        elif full not in self.known_files and not qualified:
                            if len(unclassified)<git.MAX_FAILURE_PATHS:unclassified.append((full,'file'))
                            continue
                    if stat.S_ISDIR(info.st_mode):
                        self.signatures[full]=signature(info)
                        entries.append(dict(path=rel,type='dir',mode=mode))
                    elif stat.S_ISREG(info.st_mode):
                        measured=self.measure(path,info);self.measured[full]=measured
                        entries.append(dict(path=rel,type='file',mode=mode,size=info.st_size,ino=info.st_ino,mtime_ns=info.st_mtime_ns))
                        if boundaries and name.endswith(alpha_source.BUILD_FILE_SUFFIXES):
                            if info.st_size>16 << 20:raise git.Refused('build-definition-scan-bound')
                            raw=readers.read_regular(str(self.source),str(path),16 << 20)
                            self.bytes+=len(raw)
                            if self.bytes>self.limits.bytes:raise git.Refused('source-byte-bound')
                            if hashlib.sha256(raw).hexdigest()!=measured['sha256']:
                                raise git.Refused('selected-file-changed')
                            if any(marker.encode() in raw for b in boundaries for marker in b['referenceMarkers']):
                                raise git.Refused('excluded-cache-referenced-by-build')
                    elif stat.S_ISLNK(info.st_mode):
                        text=self.link_text(name,parent)
                        if signature(info)!=signature(os.stat(name,dir_fd=parent,follow_symlinks=False)):
                            raise git.Refused('selected-link-changed')
                        kind,target,exists=self.resolve_link(base,rel,text)
                        if exists and alpha_source.inside_excluded(target,policy):
                            raise git.Refused('selected-link-reaches-exclusion')
                        self.measured[full]=dict(link=text,blob=git.object_id('blob',os.fsencode(text)))
                        self.signatures[full]=signature(info)
                        entries.append(dict(path=rel,type='link',text=text,kind=kind,target=target,exists=exists))
                    else:raise git.Refused('selected-special-file',path=full)
                if unclassified:
                    raise git.Refused('unclassified-selected-'+unclassified[0][1],path=unclassified[0][0],
                        paths=[name for name,_kind in unclassified])
        finally:os.close(rootfd)
        return dict(entries=entries,excluded=excluded,problems=[])

    def verify_namespace(self):
        """Catch ordinary mutation/addition/removal during the observation, without rereading payloads."""
        for relative,expected in self.signatures.items():
            self.check();path=self.source/relative
            if relative=='.':
                fd=readers._walk_dir(readers._body(str(self.source)))
                try:observed=signature(os.fstat(fd))
                finally:os.close(fd)
            else:
                fd=readers._walk_dir(readers._body(str(path.parent)))
                try:observed=signature(os.stat(path.name,dir_fd=fd,follow_symlinks=False))
                finally:os.close(fd)
            if observed!=expected:raise git.Refused('source-namespace-changed')


def manifest_projects(raw):
    if not isinstance(raw,bytes) or not 0<len(raw)<=4 << 20:raise git.Refused('manifest-byte-bound')
    try:document=ET.fromstring(raw)
    except ET.ParseError:raise git.Refused('manifest-xml-differs') from None
    if document.tag!='manifest' or any(document.findall(tag) for tag in ('include','submanifest','extend-project','remove-project')):
        raise git.Refused('resolved-manifest-required')
    projects=[];transfers=[];seen=set()
    for node in document.findall('project'):
        if node.findall('project'):raise git.Refused('nested-manifest-project-not-supported')
        path=git.relative(node.get('path') or node.get('name'));revision=git.oid(node.get('revision'))
        if path in seen:raise git.Refused('duplicate-manifest-project-path')
        seen.add(path);projects.append((path,revision))
        for child in node:
            if child.tag not in ('copyfile','linkfile'):continue
            transfers.append(dict(kind=child.tag,project=path,source=git.relative(child.get('src')),
                                  destination=git.relative(child.get('dest'))))
    if not projects:raise git.Refused('manifest-projects-absent')
    return projects,transfers


def expected_files(source,manifest,reader,limits):
    projects,transfers=manifest_projects(manifest);expected={};pinned_directories=set();gitlinks={}
    project_pins=dict(projects)
    for path,revision in projects:
        try:
            fd=readers._walk_dir(readers._body(str(source/path)));os.close(fd)
        except (OSError,readers._Refused) as error:
            raise git.Refused('pinned-project-directory-unavailable',path=path) from error
        try:
            rows=reader.project(source/path,revision,max_entries=limits.entries-len(expected)-len(pinned_directories),allow_gitlinks=True)
        except git.Refused as error:
            raise git.Refused(error.code,path=path+('/'+error.path if error.path else '')) from None
        for relative,row in rows.items():
            name=path+'/'+relative
            if row['kind']=='commit':
                if name in project_pins and project_pins[name]!=row['oid']:
                    raise git.Refused('gitlink-manifest-revision-differs',path=name)
                if name not in project_pins and any(p.startswith(name+'/') for p in project_pins):
                    raise git.Refused('gitlink-parent-not-manifest-pinned',path=name)
                gitlinks[name]=dict(oid=row['oid'],manifestPinned=name in project_pins)
            if row['kind'] in ('tree','commit'):
                pinned_directories.add(name)
                if len(expected)+len(pinned_directories)>limits.entries:raise git.Refused('expected-source-entry-bound')
                continue
            if name in expected:raise git.Refused('overlapping-pinned-project-bytes')
            expected[name]=dict(row,path=name,origin='git')
            if len(expected)+len(pinned_directories)>limits.entries:raise git.Refused('expected-source-entry-bound')
    for transfer in transfers:
        src=transfer['project']+'/'+transfer['source'];dest=transfer['destination']
        if dest in expected:raise git.Refused('manifest-transfer-overlaps-pinned-file')
        if transfer['kind']=='copyfile':
            original=expected.get(src)
            if original is None or original['mode']=='120000':raise git.Refused('manifest-copy-source-not-pinned-file')
            expected[dest]=dict(original,path=dest,origin='manifest-copy')
        else:
            if src not in expected and src not in pinned_directories:
                raise git.Refused('manifest-link-source-not-pinned')
            text=os.path.relpath(src,str(Path(dest).parent))
            expected[dest]=dict(path=dest,mode='120000',oid=git.object_id('blob',os.fsencode(text)),
                                origin='manifest-link')
    if len(expected)+len(pinned_directories)>limits.entries:raise git.Refused('expected-source-entry-bound')
    return projects,expected,pinned_directories,gitlinks


def shared_engine():
    spec=importlib.util.spec_from_file_location('mustang_bootstrap_shared_source',ROOT/'scripts/blazer-build-only.py')
    engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
    return engine


def catalog(rows,prefix):
    digest=hashlib.sha256();count=0;total=0
    for name,row in rows:
        if not name.startswith(prefix+'/'):continue
        count+=1;item=dict(row,path=name[len(prefix)+1:]);total+=row.get('sizeBytes',0)
        digest.update(canonical(item))
    return dict(sha256=digest.hexdigest(),entries=count,fileBytes=total,complete=True,
                method='sorted-tree-path-mode-link-and-file-sha256-v1')


def source_policy(recipe,root):
    """Capture one bounded policy and reject resource-path changes before any source read."""
    pointer='os/release-pipeline/cuttlefish-alpha.json preparation.exclusions'
    spec=recipe.get('preparation',{}).get('source',{})
    if recipe.get('adevtool',{}).get('depsDir')!='out_adevtool_deps' \
            or recipe['adevtool'].get('depsTools')!=list(deps.HOST_TOOLS) \
            or spec.get('includePaths')!=['out_adevtool_deps/adevtool_revision','out_adevtool_deps/host/linux-x86'] \
            or spec.get('exclusionsFrom')!=pointer:
        raise git.Refused('unsupported-source-selection-contract')
    data=json.loads(readers.read_regular(str(root),str(Path(root)/pointer.split()[0]),4 << 20))
    policy=data['preparation']['exclusions'];boundaries=policy.get('cacheBoundaries',[])
    if policy.get('rootNames')!=['.repo','keys','releases','out'] \
            or policy.get('rootPrefixes')!=['out-','out_'] or policy.get('anywhereNames')!=['.git','.repo'] \
            or policy.get('externalLinks')!=[] or len(boundaries)!=1 \
            or boundaries[0].get('path')!='vendor/adevtool/dl/unpacked' \
            or boundaries[0].get('pruneMarker')!='vendor/adevtool/dl/.find-ignore' \
            or boundaries[0].get('referenceMarkers')!=['adevtool/dl/unpacked','dl/unpacked/']:
        raise git.Refused('unsupported-source-exclusion-policy')
    return pointer,policy


def inspect_source(source,manifest,vendor,host,recipe,*,root=ROOT,limits=None,deadline=None):
    """Check selected bytes and prepared payloads. Identity/bootstrap/admission stay false."""
    started=time.monotonic()
    source=Path(source);limits=(limits or Limits()).validate();observation=Observation(source,limits,deadline=deadline)
    pointer,policy=source_policy(recipe,root);engine=shared_engine()
    def captured_policy(checkout,reference):
        if Path(checkout)!=Path(root) or reference!=pointer:raise git.Refused('unexpected-source-policy-pointer')
        return policy
    engine.pointer=captured_policy
    reader=git.LocalGit(observation.deadline,output_bytes=limits.git_output_bytes,
        total_output_bytes=limits.git_total_output_bytes,command_seconds=limits.git_command_seconds)
    projects,expected,pinned_directories,gitlinks=expected_files(source,manifest,reader,limits)
    adev=[revision for path,revision in projects if path=='vendor/adevtool']
    if adev!=[recipe['pins']['vendor/adevtool']]:raise git.Refused('manifest-adevtool-pin-differs')
    generated=('vendor/google_devices/mustang','vendor/adevtool/node_modules')
    depsdir=recipe['adevtool']['depsDir'];host_prefix=depsdir+'/host/linux-x86';marker=depsdir+'/adevtool_revision'
    directories={str(parent) for name in expected for parent in Path(name).parents if str(parent)!='.'}
    directories.update(pinned_directories)
    directories.update(path for path,_revision in projects)
    directories.update(str(parent) for path,_revision in projects for parent in Path(path).parents if str(parent)!='.')
    directories.update(str(parent) for name in (*generated,host_prefix,marker) for parent in Path(name).parents if str(parent)!='.')
    directories.update(generated);directories.add(host_prefix)
    observation.empty_gitlinks={name for name,row in gitlinks.items() if not row['manifestPinned']}
    observation.known_files=set(expected)|{marker};observation.known_directories=directories
    observation.prepared_prefixes=(*generated,host_prefix)
    selection=engine.source_selection(recipe,source,Path(root),planner=observation.plan)
    if selection['problems']:raise git.Refused('shared-copy-selection-refused')
    # The unchanged selection helper independently reads this exact successful marker once.
    observation.bytes+=len(recipe['pins']['vendor/adevtool'].encode())+1
    if observation.bytes>limits.bytes:raise git.Refused('source-byte-bound')
    entries=selection['entries'];names=[row['path'] for row in entries]
    if len(names)>limits.entries or len(names)!=len(set(names)):raise git.Refused('selected-entry-bound-or-duplicate')
    inventory=[];payload_rows=[];seen_expected=set();host_rows={};pinned_count=0;prepared_count=0
    for entry in entries:
        observation.check();name=entry['path'];kind=entry['type']
        in_generated=any(name==prefix or name.startswith(prefix+'/') for prefix in generated)
        in_host=name==host_prefix or name.startswith(host_prefix+'/') or name==marker
        known=expected.get(name)
        if kind=='dir':
            if name not in directories and not in_generated and not in_host:raise git.Refused('unclassified-selected-directory')
            row=dict(directory=True,mode=entry['mode'])
        else:
            measured=observation.measured.get(name)
            if measured is None:
                # source_selection adds the fixed dependency marker outside its directory planner.
                if name!=marker:raise git.Refused('selected-measurement-absent')
                measured=observation.measure(source/name);observation.measured[name]=measured
            if known is not None:
                if measured['blob']!=known['oid'] or (known['mode']=='120000')!=(kind=='link') \
                        or (kind=='file' and bool(measured['mode']&0o100)!=(known['mode']=='100755')):
                    raise git.Refused('selected-pinned-blob-or-mode-differs')
                seen_expected.add(name);pinned_count+=1
            elif not in_generated and not in_host:raise git.Refused('unclassified-selected-file',path=name)
            if in_generated or in_host:prepared_count+=1
            row={k:v for k,v in measured.items() if k!='blob'}
        payload_rows.append((name,row));inventory.append(dict(row,path=name))
        if name.startswith(host_prefix+'/') and kind!='dir':host_rows[name[len(depsdir)+1:]]=row
    for name in expected:
        if name not in seen_expected and not alpha_source.inside_excluded(name,policy):
            raise git.Refused('selected-pinned-file-missing')
    for key,prefix in (('vendorTree',generated[0]),('nodeModules',generated[1]),('adevtoolSource','vendor/adevtool/src')):
        if catalog(payload_rows,prefix)!=vendor.get(key):raise git.Refused('prepared-vendor-catalog-differs')
    measured=observation.measured
    fingerprint=lambda name:{k:v for k,v in measured[name].items() if k!='blob'}
    if fingerprint('vendor/google_devices/mustang/mustang.mk')!=vendor.get('productMakefile') \
            or fingerprint(marker)!=host.get('revisionMarker') or host_rows!=host.get('distribution'):
        raise git.Refused('prepared-product-or-host-distribution-differs')
    for key,relative in (('binRun','bin/run'),('packageJson','package.json'),('yarnLock','yarn.lock')):
        if fingerprint('vendor/adevtool/'+relative)!=vendor.get('sourceLoader',{}).get(key):
            raise git.Refused('prepared-source-loader-differs')
    for project,revision in projects:reader.check_head(source/project,revision)
    observation.verify_namespace()
    inventory.sort(key=lambda row:row['path'])
    return dict(schema='roseos.mustang-bootstrap-source-candidate.v1',success=True,
        manifestSha256=hashlib.sha256(manifest).hexdigest(),projectCount=len(projects),
        gitlinkBoundaryCount=len(gitlinks),gitlinkPolicy='manifest-matched-child-or-no-selected-content',
        expectedEntryCount=len(expected)+len(pinned_directories),
        selectedEntryCount=len(inventory),selectedPinnedEntries=pinned_count,selectedPreparedEntries=prepared_count,
        selectedManifestTransferEntries=sum(expected[name]['origin']!='git' for name in seen_expected),
        selectedInventorySha256=hashlib.sha256(canonical(inventory)).hexdigest(),sourceDataBytesRead=observation.bytes,
        portableInventorySha256=portable_inventory(inventory,source),
        copyRecipeSha256=hashlib.sha256(canonical(recipe)).hexdigest(),
        copyExclusionPolicySha256=hashlib.sha256(canonical(policy)).hexdigest(),
        gitOutputBytesRead=reader.bytes_read,
        excludedEntryCount=len(observation.excluded),excludedContentUnchecked=True,
        selectedPinnedBytesVerified=True,preparedPayloadsVerified=True,gitIndexTrusted=False,
        preparedReceiptProvenanceVerified=False,
        selectedNamespaceReadback=True,
        guestIdentityObserved=False,fullBootstrapVerified=False,pristine=False,
        dataFilesystemIdentityObserved=False,runtimeWired=False,readyForControlledStart=False,
        pristineRuntimeAttestation=False,otaAdmitted=False,signingStarted=False,
        bounds=asdict(limits),boundsKind='proposal-ceilings-not-measurements',
        elapsedSeconds=round(time.monotonic()-started,3))


def observe_prepared(lane,cfg,run,*,root=ROOT,limits=None):
    """Unwired library adapter. A future controlled guest caller must establish identity."""
    paths=mustang_paths.layout(lane)
    with locks.shared(paths['lockPath'],expected=paths['lockPath']):
        return _observe_prepared_locked(lane,cfg,run,root=root,limits=limits)


def _observe_prepared_locked(lane,cfg,run,*,root=ROOT,limits=None,deadline=None):
    """Internal collector integration; caller must hold the existing canonical lock."""
    paths=mustang_paths.layout(lane);limits=(limits or Limits()).validate()
    if not isinstance(run,dict) or any(not re.fullmatch(r'[1-9][0-9]{0,19}',str(run.get(k,'')))
                                     for k in ('runId','runAttempt')) \
            or run.get('workflow')!='Immortal-Protocols/RoseOS-1/'+lane['workflow']+'@refs/heads/main':
        raise git.Refused('declared-observation-run-differs')
    git.oid(run.get('source'))
    # Bound these checkout inputs before existing binding helpers read them.
    manifest=readers.read_regular(str(root),str(Path(root)/'os/pinned-manifest.xml'),4 << 20)
    recipe=json.loads(readers.read_regular(str(root),str(Path(root)/'os/build-only/mustang.json'),4 << 20))
    vendor,host=prepared.binding(lane,cfg,root)
    identity=dict(builder=lane['vm'],diskUuid=cfg['dataFilesystemUuid'],run=run)
    v=inputs.receipt_on_disk(vendor,paths['storageRoot'],'vendor','vendor-preparation-receipt.json',identity)
    h=inputs.receipt_on_disk(host,paths['storageRoot'],'hostdeps','hostdeps-receipt.json',identity)
    if deadline is not None and time.monotonic()>=deadline:raise git.Refused('source-proof-deadline')
    result=inspect_source(paths['sourceRoot'],manifest,v,h,recipe,root=root,limits=limits,deadline=deadline)
    return dict(result,sourceRoot=str(paths['sourceRoot']),declaredBuilder=lane['vm'],
                observationRun=run,runIdentityObserved=False,preparedReceiptProvenanceVerified=True,
                vendorReceiptSha256=vendor['receiptSha256'],hostDependenciesReceiptSha256=host['receiptSha256'])


def verify_copy(source,candidate,selection,observed,*,limits=None):
    """Bridge the actual shared copy and its readback to the observed pinned bytes.

    Call before allocation/Make, while holding the dedicated canonical lock.
    Git metadata subsequently installed by git-meta is excluded; source payload
    names, modes, bytes and link text must exactly match the copy record.
    """
    limits=(limits or Limits()).validate();source=Path(source);candidate=Path(candidate)
    path=candidate.parent/(candidate.name+'.selection.tsv')
    if selection.get('path')!=str(path) or observed.get('sourceRoot')!=str(source) \
            or observed.get('success') is not True or observed.get('selectedPinnedBytesVerified') is not True \
            or observed.get('preparedReceiptProvenanceVerified') is not True:
        raise git.Refused('full-run-source-copy-binding-differs')
    def unescape(raw):
        result=bytearray();i=0;table={ord('t'):9,ord('n'):10,ord('r'):13,ord('\\'):92}
        while i<len(raw):
            if raw[i]==92:
                i+=1
                if i==len(raw) or raw[i] not in table:raise git.Refused('copy-record-escape-differs')
                result.append(table[raw[i]])
            else:result.append(raw[i])
            i+=1
        return os.fsdecode(bytes(result))
    fd,info=readers._open_regular(str(candidate.parent),str(path));rows=[];names=set();digest=hashlib.sha256();size=0
    if info.st_size>512 << 20:
        os.close(fd);raise git.Refused('copy-record-byte-bound')
    with os.fdopen(fd,'rb') as stream:
        for line in iter(lambda:stream.readline((1 << 20)+1),b''):
            if len(line)>1 << 20 or not line.endswith(b'\n'):raise git.Refused('copy-record-line-bound')
            digest.update(line);size+=len(line);fields=line[:-1].split(b'\t')
            if len(fields)<2:raise git.Refused('copy-record-fields-differ')
            name=git.relative(unescape(fields[0]));kind=fields[1]
            if name in names or len(names)>=limits.entries:raise git.Refused('copy-record-entry-bound')
            names.add(name)
            if kind in (b'dir',b'file'):
                if len(fields)!=(3 if kind==b'dir' else 5) or not re.fullmatch(rb'[0-7]{1,4}',fields[2]):
                    raise git.Refused('copy-record-fields-differ')
                mode=int(fields[2],8)
                if mode&0o7000:raise git.Refused('copy-record-mode-differs')
                if kind==b'dir':row=dict(directory=True,mode=mode)
                else:
                    if not re.fullmatch(rb'(0|[1-9][0-9]*)',fields[3]) or not re.fullmatch(rb'[0-9a-f]{64}',fields[4]):
                        raise git.Refused('copy-record-fields-differ')
                    row=dict(mode=mode,sizeBytes=int(fields[3]),sha256=fields[4].decode())
            elif kind==b'link' and len(fields)==3:row=dict(link=unescape(fields[2]))
            else:raise git.Refused('copy-record-fields-differ')
            rows.append(dict(row,path=name))
        if signature(info)!=signature(os.fstat(stream.fileno())):raise git.Refused('copy-record-changed')
    if digest.hexdigest()!=selection.get('sha256') or size!=selection.get('bytes') or len(rows)!=selection.get('lines') \
            or len(rows)!=observed.get('selectedEntryCount') \
            or portable_inventory(rows,candidate)!=observed.get('portableInventorySha256'):
        raise git.Refused('copied-selection-differs-from-observed-source')
    check=Observation(candidate,limits)
    check.known_files={row['path'] for row in rows if not row.get('directory')}
    check.known_directories={row['path'] for row in rows if row.get('directory')}
    policy=dict(rootNames=[],rootPrefixes=[],anywhereNames=['.git','.repo'],externalLinks=[],cacheBoundaries=[])
    planned=check.plan(candidate,policy);actual=[]
    for entry in planned['entries']:
        name=entry['path']
        value=dict(directory=True,mode=entry['mode']) if entry['type']=='dir' else {
            k:v for k,v in check.measured[name].items() if k!='blob'}
        actual.append(dict(value,path=name))
    check.verify_namespace()
    if portable_inventory(actual,candidate)!=observed['portableInventorySha256']:
        raise git.Refused('copied-source-readback-differs')
    return dict(schema='roseos.mustang-full-run-source-copy.v1',sourceCopyVerified=True,
        selectedEntryCount=len(actual),portableInventorySha256=observed['portableInventorySha256'],
        selectionRecordSha256=selection['sha256'],copyDataBytesRead=check.bytes,
        gitMetadataExcluded=True,otaAdmitted=False,pristine=False)


def main():
    import argparse
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(dict(schema='roseos.mustang-bootstrap-source-plan.v1',execute=False,runtimeWired=False,
        readyForControlledStart=False,fullBootstrapVerified=False,pristine=False,otaAdmitted=False,
        bounds=asdict(Limits()),boundsKind='proposal-ceilings-not-measurements'),sort_keys=True))


if __name__=='__main__':main()
