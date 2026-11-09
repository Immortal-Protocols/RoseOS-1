"""Offline synthetic repositories only. No native VM, archive program, access or signer."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'scripts'),str(Path(__file__).resolve().parent)]
import mustang_bootstrap_git as git
import mustang_bootstrap_proof as proof
import mustang_vendor_prep as vendor_prep


class SourceProofTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name);self.source=self.home/'source';self.source.mkdir()
        self.root=self.home/'checkout';(self.root/'os/build-only').mkdir(parents=True)
        (self.root/'os/release-pipeline').mkdir()
        shutil.copyfile(ROOT/'os/release-pipeline/cuttlefish-alpha.json',self.root/'os/release-pipeline/cuttlefish-alpha.json')
        self.recipe=json.loads((ROOT/'os/build-only/mustang.json').read_text())
        adev=self.source/'vendor/adevtool';(adev/'src').mkdir(parents=True);(adev/'bin').mkdir();(adev/'dl').mkdir()
        (adev/'src/index.ts').write_bytes(b'tracked typescript fixture\n')
        (adev/'bin/run').write_bytes(b'NEVER EXECUTE: source-loader fixture\n');(adev/'bin/run').chmod(0o755)
        for name in ('package.json','yarn.lock'):(adev/name).write_bytes(b'tracked fixture\n')
        (adev/'.gitignore').write_text('node_modules/\nlib/\ndl/*\n!dl/.find-ignore\n')
        (adev/'dl/.find-ignore').write_bytes(b'')
        self.adev_revision=self.init_project(adev)
        make=self.source/'build/make';make.mkdir(parents=True)
        (make/'envsetup.sh').write_text('NEVER EXECUTE: envsetup fixture\n')
        (make/'README').write_text('pinned copy-file fixture\n')
        (make/'tab\tand\nnewline').write_bytes(b'NUL-delimited metadata fixture')
        (make/'a').mkdir();(make/'a/child').write_bytes(b'directory sort fixture')
        (make/'a.c').write_bytes(b'Git sorts this before directory a')
        (make/'Build.mk').write_bytes(b'# tracked build definition fixture\n')
        self.make_revision=self.init_project(make)
        empty=self.source/'empty-project';empty.mkdir();self.empty_revision=self.init_project(empty)
        self.projects=[('vendor/adevtool',self.adev_revision),('build/make',self.make_revision),('empty-project',self.empty_revision)]
        document=ET.Element('manifest')
        for path,revision in self.projects:
            project=ET.SubElement(document,'project',name=path,path=path,revision=revision)
            if path=='build/make':
                ET.SubElement(project,'linkfile',src='envsetup.sh',dest='build/envsetup.sh')
                ET.SubElement(project,'copyfile',src='README',dest='README.fixture')
        self.manifest=ET.tostring(document)
        (self.root/'os/pinned-manifest.xml').write_bytes(self.manifest)
        (self.source/'build/envsetup.sh').symlink_to('make/envsetup.sh')
        shutil.copyfile(make/'README',self.source/'README.fixture')
        self.recipe['pins']['vendor/adevtool']=self.adev_revision
        (self.root/'os/build-only/mustang.json').write_text(json.dumps(self.recipe))
        unpacked=adev/'dl/unpacked';unpacked.mkdir();os.mkfifo(unpacked/'excluded-fifo')
        generated=self.source/'vendor/google_devices/mustang';generated.mkdir(parents=True)
        (generated/'mustang.mk').write_bytes(b'generated fixture\n')
        modules=adev/'node_modules';(modules/'pkg').mkdir(parents=True);(modules/'pkg/data').write_bytes(b'dependency fixture')
        (modules/'alias').symlink_to('pkg')
        out=self.source/self.recipe['adevtool']['depsDir'];binary=out/'host/linux-x86/bin';binary.mkdir(parents=True)
        for name in self.recipe['adevtool']['depsTools']:
            p=binary/name;p.write_bytes(b'NEVER EXECUTE: host dependency fixture');p.chmod(0o755)
        marker=out/'adevtool_revision';marker.write_text(self.adev_revision+'\n')
        deadline=time.monotonic()+30
        self.vendor={key:vendor_prep.catalog(self.source,self.source/path,deadline) for key,path in
            (('vendorTree','vendor/google_devices/mustang'),('nodeModules','vendor/adevtool/node_modules'),
             ('adevtoolSource','vendor/adevtool/src'))}
        self.vendor['productMakefile']=proof.deps.fingerprint(self.source,generated/'mustang.mk',deadline)
        self.vendor['sourceLoader']={key:proof.deps.fingerprint(self.source,adev/path,deadline) for key,path in
            (('binRun','bin/run'),('packageJson','package.json'),('yarnLock','yarn.lock'))}
        self.host=dict(revisionMarker=proof.deps.fingerprint(self.source,marker,deadline),
            distribution=proof.deps.inventory(self.source,out,deadline))

    def command(self,directory,*arguments,env=None):
        safe=dict(git.environment(),GIT_AUTHOR_NAME='Fixture',GIT_AUTHOR_EMAIL='fixture@example.invalid',
                  GIT_COMMITTER_NAME='Fixture',GIT_COMMITTER_EMAIL='fixture@example.invalid',GIT_DEFAULT_HASH='sha1')
        if env:safe.update(env)
        return subprocess.check_output(['/usr/bin/git','-C',str(directory),*arguments],env=safe,stderr=subprocess.DEVNULL)

    def init_project(self,directory):
        self.command(directory,'init','--quiet');self.command(directory,'add','--all')
        self.command(directory,'commit','--allow-empty','--quiet','-m','synthetic source fixture')
        return self.command(directory,'rev-parse','HEAD').decode().strip()

    def inspect(self,**kwargs):
        return proof.inspect_source(self.source,self.manifest,self.vendor,self.host,self.recipe,root=self.root,**kwargs)

    def test_complete_selected_bytes_and_prepared_payloads_match_without_bootstrap_admission(self):
        before=self.command(self.source/'vendor/adevtool','status','--porcelain','--untracked-files=no')
        result=self.inspect()
        self.assertEqual(result['projectCount'],3);self.assertTrue(result['selectedPinnedBytesVerified'])
        self.assertTrue(result['preparedPayloadsVerified']);self.assertGreater(result['selectedPreparedEntries'],14)
        self.assertEqual(result['selectedManifestTransferEntries'],2)
        self.assertEqual(before,self.command(self.source/'vendor/adevtool','status','--porcelain','--untracked-files=no'))
        for key in ('guestIdentityObserved','fullBootstrapVerified','pristine','pristineRuntimeAttestation',
                    'otaAdmitted','signingStarted','gitIndexTrusted','preparedReceiptProvenanceVerified'):
            self.assertIs(result[key],False)
        self.assertTrue(result['excludedContentUnchecked'])

    def test_changed_pinned_blob_cannot_be_hidden_by_assume_unchanged_or_skip_worktree(self):
        path=self.source/'build/make/README';original=path.read_bytes()
        for flag in ('--assume-unchanged','--skip-worktree'):
            self.command(path.parent,'update-index',flag,'README');path.write_bytes(b'hidden changed bytes')
            self.assertEqual(self.command(path.parent,'status','--porcelain','--untracked-files=no'),b'')
            with self.subTest(flag=flag),self.assertRaisesRegex(git.Refused,'pinned-blob-or-mode'):self.inspect()
            path.write_bytes(original)
            self.command(path.parent,'update-index',flag.replace('--','--no-',1),'README')

    def test_unknown_ignored_file_or_empty_directory_is_not_silently_selected(self):
        adev=self.source/'vendor/adevtool'
        self.command(adev,'config','core.excludesFile',str(self.home/'ignore'))
        (self.home/'ignore').write_text('unqualified*\n')
        for directory in (False,True):
            p=adev/('unqualified-dir' if directory else 'unqualified-file')
            p.mkdir() if directory else p.write_bytes(b'unknown ignored content')
            with self.subTest(directory=directory),self.assertRaisesRegex(git.Refused,'unclassified-selected'):self.inspect()
            p.rmdir() if directory else p.unlink()

    def test_generated_vendor_dependency_and_host_changes_refuse(self):
        for relative in ('vendor/google_devices/mustang/mustang.mk','vendor/adevtool/node_modules/pkg/data',
                         'out_adevtool_deps/host/linux-x86/bin/aapt2'):
            p=self.source/relative;before=p.read_bytes();p.write_bytes(b'changed prepared bytes')
            with self.subTest(relative=relative),self.assertRaises(git.Refused):self.inspect()
            p.write_bytes(before)

    def test_manifest_copy_link_missing_file_and_executable_mode_changes_refuse(self):
        copyfile=self.source/'README.fixture';data=copyfile.read_bytes();copyfile.write_bytes(b'wrong copy')
        with self.assertRaisesRegex(git.Refused,'pinned-blob-or-mode'):self.inspect()
        copyfile.write_bytes(data)
        link=self.source/'build/envsetup.sh';link.unlink();link.symlink_to('make/README')
        with self.assertRaisesRegex(git.Refused,'pinned-blob-or-mode'):self.inspect()
        link.unlink();link.symlink_to('make/envsetup.sh')
        file=self.source/'vendor/adevtool/bin/run';file.chmod(0o644)
        with self.assertRaisesRegex(git.Refused,'pinned-blob-or-mode'):self.inspect()
        file.chmod(0o755)
        (self.source/'build/make/README').unlink()
        with self.assertRaisesRegex(git.Refused,'pinned-file-missing'):self.inspect()

    def test_selected_special_file_bounds_and_cache_reference_refuse(self):
        fifo=self.source/'unexpected-fifo';os.mkfifo(fifo)
        with self.assertRaisesRegex(git.Refused,'unclassified-selected-file'):self.inspect()
        fifo.unlink()
        with self.assertRaisesRegex(git.Refused,'entry-bound'):self.inspect(limits=proof.Limits(entries=1))
        with self.assertRaisesRegex(git.Refused,'source-byte-bound'):self.inspect(limits=proof.Limits(bytes=1))
        (self.source/'build/make/Build.mk').write_text('vendor/adevtool/dl/unpacked')
        with self.assertRaisesRegex(git.Refused,'excluded-cache-referenced'):self.inspect()

    def test_git_reads_never_invoke_configured_filters_hooks_or_inherited_credentials(self):
        project=self.source/'build/make';marker=self.home/'source-program-ran'
        self.command(project,'config','filter.bad.clean','touch '+str(marker))
        self.command(project,'config','core.fsmonitor','touch '+str(marker))
        self.command(project,'config','core.hooksPath',str(self.home/'hooks'))
        # Attributes are untracked and rejected, but never interpreted by the reader.
        (project/'.gitattributes').write_text('* filter=bad\n')
        with patch.dict(os.environ,dict(GH_TOKEN='not-forwarded',GIT_DIR='/not-the-source',BASH_ENV='/never-source')):
            reader=git.LocalGit(time.monotonic()+30)
            rows=reader.project(project,self.make_revision,max_entries=100)
        self.assertIn('README',rows);self.assertFalse(marker.exists())
        self.assertNotIn('GH_TOKEN',git.environment());self.assertNotIn('GIT_DIR',git.environment())
        self.assertEqual(git.environment()['GIT_ALLOW_PROTOCOL'],'')

    def test_corrupt_commit_and_tree_listing_are_rejected(self):
        project=self.source/'build/make';reader=git.LocalGit(time.monotonic()+30)
        commit=reader.read(project,'commit',self.make_revision);listing=reader.read(project,'tree',self.make_revision)
        with self.assertRaisesRegex(git.Refused,'commit-object-differs'):
            git.tree_rows(commit+b'x',listing,self.make_revision,max_entries=100)
        altered=listing.replace(b'README',b'READMX')
        with self.assertRaisesRegex(git.Refused,'reconstructed-tree-differs'):
            git.tree_rows(commit,altered,self.make_revision,max_entries=100)
        with self.assertRaisesRegex(git.Refused,'operation-not-allowed'):
            reader.read(project,'status')

    def test_component_resolution_does_not_hide_an_escape_behind_alias_parent(self):
        base=self.source/'links';base.mkdir();(base/'x/y').mkdir(parents=True)
        (base/'x/value').write_bytes(b'correct target');(base/'value').write_bytes(b'lexical target')
        (base/'alias').symlink_to('x/y')
        obs=proof.Observation(self.source,proof.Limits())
        self.assertEqual(obs.resolve_link(base,'link','alias/../value'),('relative','x/value',True))
        outside=self.home/'outside';outside.mkdir();(outside/'secret').write_bytes(b'never read')
        (base/'escape').symlink_to('../../outside')
        with self.assertRaisesRegex(git.Refused,'selected-link-escapes'):
            obs.resolve_link(base,'link','escape/../value')
        (base/'cycle').symlink_to('cycle')
        with self.assertRaisesRegex(git.Refused,'selected-link-cycle'):
            obs.resolve_link(base,'link','cycle')

    def test_plan_has_no_runtime_or_execution_switch(self):
        result=subprocess.run([sys.executable,str(ROOT/'scripts/mustang_bootstrap_proof.py')],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0);plan=json.loads(result.stdout)
        for key in ('execute','runtimeWired','readyForControlledStart','fullBootstrapVerified','pristine','otaAdmitted'):
            self.assertIs(plan[key],False)
        rejected=subprocess.run([sys.executable,str(ROOT/'scripts/mustang_bootstrap_proof.py'),'--execute'],capture_output=True,timeout=10)
        self.assertNotEqual(rejected.returncode,0)

    def test_nested_tree_hashes_and_git_directory_sort_order_are_verified(self):
        project=self.source/'build/make';reader=git.LocalGit(time.monotonic()+30)
        commit=reader.read(project,'commit',self.make_revision);listing=reader.read(project,'tree',self.make_revision)
        result=git.tree_rows(commit,listing,self.make_revision,max_entries=100)
        self.assertIn('a/child',result);self.assertIn('a.c',result)
        with self.assertRaisesRegex(git.Refused,'reconstructed-tree-differs'):
            git.tree_rows(commit,listing.replace(b'a/child',b'a/other'),self.make_revision,max_entries=100)

    def test_git_output_deadline_and_entry_bounds_are_enforced(self):
        project=self.source/'build/make'
        with self.assertRaisesRegex(git.Refused,'git-output-bound'):
            git.LocalGit(time.monotonic()+30,output_bytes=1).read(project,'head')
        with self.assertRaisesRegex(git.Refused,'source-proof-deadline'):
            git.LocalGit(time.monotonic()-1).read(project,'head')
        for limits in (proof.Limits(entries=True),proof.Limits(seconds=0),proof.Limits(bytes=(150 << 30)+1)):
            with self.subTest(limits=limits),self.assertRaises(git.Refused):limits.validate()
        reader=git.LocalGit(time.monotonic()+30,total_output_bytes=80)
        reader.read(project,'head')
        with self.assertRaisesRegex(git.Refused,'git-total-output-bound'):reader.read(project,'head')

    def test_source_namespace_addition_after_measurement_is_detected(self):
        original=git.LocalGit.check_head;calls=[]
        def check(reader,project,revision):
            original(reader,project,revision);calls.append(project)
            if len(calls)==2*len(self.projects):
                (self.source/'added-after-walk').write_bytes(b'new unclassified bytes')
        with patch.object(git.LocalGit,'check_head',new=check):
            with self.assertRaisesRegex(git.Refused,'source-namespace-changed'):self.inspect()

    def test_object_replacements_do_not_change_the_pinned_object_reader(self):
        project=self.source/'build/make';p=project/'README'
        p.write_bytes(b'replacement commit fixture')
        self.command(project,'add','README');self.command(project,'commit','--quiet','-m','replacement fixture')
        replacement=self.command(project,'rev-parse','HEAD').decode().strip()
        self.command(project,'reset','--hard',self.make_revision)
        self.command(project,'replace',self.make_revision,replacement)
        self.assertTrue(self.inspect()['selectedPinnedBytesVerified'])

    def test_gitlinks_and_non_sha1_repositories_refuse(self):
        project=self.source/'build/make'
        self.command(project,'update-index','--add','--cacheinfo','160000,'+self.make_revision+',submodule-fixture')
        self.command(project,'commit','--quiet','-m','unsupported gitlink fixture')
        revision=self.command(project,'rev-parse','HEAD').decode().strip()
        with self.assertRaisesRegex(git.Refused,'unsupported-git-tree-mode'):
            git.LocalGit(time.monotonic()+30).project(project,revision,max_entries=100)
        other=self.home/'sha256-repository';other.mkdir()
        self.command(other,'init','--quiet','--object-format=sha256')
        self.command(other,'commit','--allow-empty','--quiet','-m','sha256 fixture')
        with self.assertRaisesRegex(git.Refused,'git-object-format-differs'):
            git.LocalGit(time.monotonic()+30).project(other,self.make_revision,max_entries=100)

    def test_missing_promisor_object_is_not_fetched(self):
        project=self.source/'build/make'
        self.command(project,'config','extensions.partialClone','origin')
        self.command(project,'config','remote.origin.promisor','true')
        self.command(project,'config','remote.origin.url','file:///definitely-not-an-authorized-source')
        object_file=project/'.git/objects'/self.make_revision[:2]/self.make_revision[2:]
        self.assertTrue(object_file.exists());object_file.unlink()
        with self.assertRaisesRegex(git.Refused,'local-git-read-failed'):
            git.LocalGit(time.monotonic()+30).read(project,'commit',self.make_revision)
        self.assertFalse(object_file.exists())

    def test_prepared_adapter_uses_existing_read_only_lock_and_exact_disk_receipts(self):
        import test_mustang_edge_gate as fixture
        lane=fixture.DedicatedPathsTest().profile(self.home)
        storage=self.home/'rose-actions';storage.mkdir()
        lock=Path(lane['lockPath']);lock.touch();lock.chmod(0o444)
        cfg=dict(dataFilesystemUuid='fixture')
        run=dict(runId='999',runAttempt='1',source='b'*40,
            workflow='Immortal-Protocols/RoseOS-1/'+lane['workflow']+'@refs/heads/main')
        def record(kind,number,payload,filename):
            old=dict(run,runId=number,source='a'*40)
            payload=dict(payload,identity=dict(builder=lane['vm'],diskUuid='fixture',run=old),success=True)
            raw=(json.dumps(payload,sort_keys=True)+'\n').encode()
            directory=storage/('mustang-'+kind+'-'+number+'-1');directory.mkdir();(directory/filename).write_bytes(raw)
            return dict(receipt=payload,receiptSha256=hashlib.sha256(raw).hexdigest(),runId=number,
                source='a'*40,stopObservation=dict(instanceId=lane['vm']['instanceId']))
        vendor=record('vendor','111',self.vendor,'vendor-preparation-receipt.json')
        host=record('hostdeps','112',self.host,'hostdeps-receipt.json')
        before=lock.stat()
        with patch.object(proof.prepared,'binding',return_value=(vendor,host)):
            result=proof.observe_prepared(lane,cfg,run,root=self.root)
            self.assertTrue(result['preparedReceiptProvenanceVerified'])
            self.assertFalse(result['guestIdentityObserved']);self.assertFalse(result['runIdentityObserved'])
            self.assertFalse(result['fullBootstrapVerified']);self.assertFalse(result['pristine'])
            path=storage/'mustang-vendor-111-1/vendor-preparation-receipt.json';path.write_text('{}')
            with self.assertRaisesRegex(ValueError,'uploaded run evidence'):
                proof.observe_prepared(lane,cfg,run,root=self.root)
        after=lock.stat()
        self.assertEqual((before.st_ino,before.st_mode,before.st_size),(after.st_ino,after.st_mode,after.st_size))
        lock.unlink()
        with self.assertRaisesRegex(ValueError,'canonical lock'):
            proof.observe_prepared(lane,cfg,run,root=self.root)

    def test_snapshot_readback_detects_a_rewritten_pinned_file_after_its_hash(self):
        original=proof.Observation.verify_namespace
        def changed(observation):
            (self.source/'build/make/README').write_bytes(b'changed after pinned-byte check')
            return original(observation)
        with patch.object(proof.Observation,'verify_namespace',new=changed):
            with self.assertRaisesRegex(git.Refused,'source-namespace-changed'):self.inspect()

    def test_unknown_selected_file_is_refused_without_reading_its_bytes(self):
        unknown=self.source/'unclassified-private-input';unknown.write_bytes(b'never opened by the observer')
        original=proof.Observation.measure
        def measured(observation,path,expected=None):
            self.assertNotEqual(path,unknown,'unknown input bytes were opened')
            return original(observation,path,expected)
        with patch.object(proof.Observation,'measure',new=measured):
            with self.assertRaisesRegex(git.Refused,'unclassified-selected-file'):self.inspect()

    def test_selected_pinned_fifo_is_refused_without_blocking(self):
        path=self.source/'build/make/README';path.unlink();os.mkfifo(path)
        with self.assertRaisesRegex(git.Refused,'selected-special-file'):self.inspect()

    def test_git_executable_mode_uses_owner_execute_bit(self):
        path=self.source/'vendor/adevtool/bin/run';path.chmod(0o654)
        with self.assertRaisesRegex(git.Refused,'pinned-blob-or-mode'):self.inspect()

    def test_git_read_timeout_ends_only_its_owned_fixture_process(self):
        original=subprocess.Popen;children=[]
        def surrogate(argv,**kwargs):
            self.assertEqual(argv[0],'/usr/bin/git')
            child=original([sys.executable,'-c','import time; time.sleep(30)'],**kwargs)
            children.append(child);return child
        started=time.monotonic()
        with patch.object(git.subprocess,'Popen',side_effect=surrogate):
            with self.assertRaisesRegex(git.Refused,'git-read-deadline'):
                git.LocalGit(time.monotonic()+10,command_seconds=1).read(self.source/'build/make','head')
        self.assertLess(time.monotonic()-started,5)
        self.assertEqual(len(children),1);self.assertIsNotNone(children[0].poll())

    def test_manifest_must_be_bounded_resolved_and_unambiguous(self):
        for value in (b'',b'x'*((4 << 20)+1),b'<manifest><include name="elsewhere"/></manifest>',
                      b'<manifest><project path="../outside" revision="'+b'a'*40+b'"/></manifest>'):
            with self.subTest(value=value[:60]),self.assertRaises(git.Refused):proof.manifest_projects(value)
        document=ET.fromstring(self.manifest);document.append(copy.deepcopy(document.find('project')))
        with self.assertRaisesRegex(git.Refused,'duplicate-manifest-project'):
            proof.manifest_projects(ET.tostring(document))

    def test_non_utf8_git_filename_round_trips_without_quoting_or_payload_execution(self):
        project=self.source/'build/make';name=os.fsdecode(b'non-utf8-\xff.dat')
        (project/name).write_bytes(b'raw filename fixture')
        self.command(project,'add','--all');self.command(project,'commit','--quiet','-m','filename fixture')
        revision=self.command(project,'rev-parse','HEAD').decode().strip()
        document=ET.fromstring(self.manifest)
        for node in document.findall('project'):
            if node.get('path')=='build/make':node.set('revision',revision)
        self.manifest=ET.tostring(document)
        rows=git.LocalGit(time.monotonic()+30).project(project,revision,max_entries=100)
        self.assertIn(name,rows);self.assertTrue(self.inspect()['selectedPinnedBytesVerified'])

    def test_bounded_planner_selects_the_same_input_paths_and_attributes_as_the_shared_copier(self):
        engine=proof.shared_engine();observation=proof.Observation(self.source,proof.Limits())
        original=engine.source_selection(self.recipe,self.source,self.root)
        bounded=engine.source_selection(self.recipe,self.source,self.root,planner=observation.plan)
        self.assertEqual(original['problems'],[]);self.assertEqual(bounded['problems'],[])
        by_path=lambda plan:{row['path']:row for row in plan['entries']}
        self.assertEqual(by_path(original),by_path(bounded))
        self.assertEqual(original['excluded'],bounded['excluded'])

    def test_recipe_and_policy_cannot_redirect_reads_or_add_exclusion_exceptions(self):
        mutations=[lambda r:r['adevtool'].update(depsDir='../outside'),
            lambda r:r['adevtool'].update(depsTools=['../../outside']),
            lambda r:r['preparation']['source'].update(includePaths=['../outside']),
            lambda r:r['preparation']['source'].update(exclusionsFrom='os/elsewhere.json data')]
        for mutate in mutations:
            recipe=copy.deepcopy(self.recipe);mutate(recipe)
            with patch.object(git.LocalGit,'project',side_effect=AssertionError('Git read preceded path validation')):
                with self.assertRaisesRegex(git.Refused,'unsupported-source-selection-contract'):
                    proof.inspect_source(self.source,self.manifest,self.vendor,self.host,recipe,root=self.root)
        policy=self.root/'os/release-pipeline/cuttlefish-alpha.json';data=json.loads(policy.read_text())
        data['preparation']['exclusions']['rootNames'].append('unknown-secret-input')
        policy.write_text(json.dumps(data))
        with patch.object(git.LocalGit,'project',side_effect=AssertionError('Git read preceded policy validation')):
            with self.assertRaisesRegex(git.Refused,'unsupported-source-exclusion-policy'):self.inspect()


if __name__=='__main__':unittest.main()
