"""The builder's Codex app writer (docs/APP-BUILDER.md §4A): one app_build session per job, returned only after the
writer confirmed it stopped. A fake app-server stands in for Codex; nothing here reaches a model."""
import os
import shutil
import subprocess
import tempfile
import textwrap
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

from rose.app_builder import toolchain
from rose.app_builder.generator import CodexGenerator, prompt
from rose.app_builder.service import Builder
from rose.app_builder.toolchain import StepFailed
from rose.tests.test_app_builder import HAS_SDK, JAVA_HOME, SDK, fake_backup


class FakeClient:
    """connect(on_event) stand-in; ``script`` maps an op to a reply or exception, and ``after_submit`` events."""

    def __init__(self, on_event, open_reply=None, open_error=None, after_submit=(), close_reply=None, exits=True):
        self.on_event, self.calls = on_event, []
        self.open_reply = open_reply or {'model': 'gpt-6.1-terra'}
        self.open_error, self.after_submit = open_error, after_submit
        self.close_reply = {'stopped': True} if close_reply is None else close_reply
        self.exits, self.closed = exits, False

    def request(self, op, session, _timeout=100, **params):
        self.calls.append((op, session, params))
        if op == 'open':
            if self.open_error:
                raise self.open_error
            return self.open_reply
        if op == 'submit':
            for e in self.after_submit:
                self.on_event({'session': session, **e} if 'session' not in e else e)
            return {}
        if op == 'close':
            if isinstance(self.close_reply, Exception):
                raise self.close_reply
            return self.close_reply

    def close(self):
        self.closed = True
        return self.exits


DONE = ({'event': 'message', 'text': 'A tip calculator.', 'phase': 'final_answer'},
        {'event': 'done', 'turn': {'id': 't', 'status': 'completed'}})


class Generator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = Path(self.tmp.name) / 'projects' / 'p1' / 'source'
        self.source.mkdir(parents=True)
        self.clients = []

    def tearDown(self):
        self.tmp.cleanup()

    def gen(self, timeout_s=5.0, **fake):
        def connect(on_event):
            c = FakeClient(on_event, **fake)
            self.clients.append(c)
            return c
        return CodexGenerator(timeout_s=timeout_s, connect=connect)

    def run_gen(self, g, kind='create', cancelled=lambda: False):
        return g(self.source, kind, 'Build a tip calculator.', cancelled)

    def test_completed_turn_returns_the_note_after_a_confirmed_stop(self):
        self.assertEqual(self.run_gen(self.gen(after_submit=DONE)), 'A tip calculator.')
        c = self.clients[0]
        op, session, params = c.calls[0]
        self.assertEqual((op, params['purpose'], params['tools'], params['cwd'], params['model']),
                         ('open', 'app_build', [], str(self.source.resolve()), 'gpt-6.1-terra'))
        self.assertNotIn('instructions', params)  # the bridge fixes them
        self.assertIn('Build a tip calculator.', c.calls[1][2]['text'])
        self.assertEqual([x[0] for x in c.calls], ['open', 'submit', 'close'])
        self.assertTrue(c.closed)

    def test_the_library_store_list_reaches_the_turn(self):
        g = self.gen(after_submit=DONE)
        g.libraries = lambda: ['org.x:y:2', 'com.example:lib:1.0']  # e.g. a RecordedStore's available
        self.run_gen(g)
        self.assertIn('com.example:lib:1.0, org.x:y:2', self.clients[0].calls[1][2]['text'])
        self.run_gen(self.gen(after_submit=DONE))  # none given: no library is offered
        self.assertNotIn('dependencies.json', self.clients[1].calls[1][2]['text'])

    def test_prompt_kinds(self):
        self.assertIn('new app', prompt('create', 'x'))
        self.assertIn('keeping their data', prompt('revise', 'x'))
        self.assertNotIn('dependencies.json', prompt('create', 'x'))  # no store: no library is offered
        for kind in ('create', 'revise'):
            text = prompt(kind, 'the request', ['com.example:lib:1.0', 'org.x:y:2'])
            self.assertIn('com.example:lib:1.0, org.x:y:2', text)
            self.assertIn('dependencies.json', text)
            self.assertTrue(text.endswith('the request'))
        with self.assertRaises(StepFailed):
            prompt('delete', 'x')

    def test_unfinished_or_failed_turns_fail(self):
        for events in (({'event': 'done', 'turn': {'status': 'interrupted'}},),
                       ({'event': 'done', 'turn': {'status': 'failed'}},),
                       ({'event': 'failure', 'message': 'App build tool boundary or budget exceeded'},),
                       ({'event': 'bridge_failure'},)):
            with self.assertRaises(StepFailed) as cm:
                self.run_gen(self.gen(after_submit=events))
            self.assertEqual(cm.exception.category, 'internal', events)
            self.assertEqual(self.clients[-1].calls[-1][0], 'close')

    def test_other_sessions_events_are_ignored(self):
        events = ({'session': 'other', 'event': 'done', 'turn': {'status': 'completed'}},) + DONE
        self.assertEqual(self.run_gen(self.gen(after_submit=events)), 'A tip calculator.')

    def test_cancel_and_timeout(self):
        with self.assertRaises(StepFailed) as cm:
            self.run_gen(self.gen(), cancelled=lambda: True)
        self.assertEqual(cm.exception.category, 'cancelled')
        with self.assertRaises(StepFailed) as cm:
            self.run_gen(self.gen(timeout_s=0.3))
        self.assertEqual(cm.exception.category, 'timeout')
        self.assertEqual(self.clients[-1].calls[-1][0], 'close')

    def test_open_refusals(self):
        with self.assertRaises(StepFailed) as cm:
            self.run_gen(self.gen(open_error=RuntimeError('research_isolation_unavailable')))
        self.assertIn('isolated', cm.exception.summary)
        self.assertEqual([x[0] for x in self.clients[-1].calls], ['open'])  # bridge already confirmed the stop
        with self.assertRaises(StepFailed):
            self.run_gen(self.gen(open_reply={'model': 'gpt-6-luna'}))
        self.assertEqual(self.clients[-1].calls[-1][0], 'close')

    def test_an_unconfirmed_stop_is_never_success(self):
        # Close not confirmed and the bridge did not exit cleanly: the writer may still be running.
        for close_reply in ({}, {'stopped': False}, TimeoutError()):
            with self.assertRaises(StepFailed) as cm:
                self.run_gen(self.gen(after_submit=DONE, close_reply=close_reply, exits=False))
            self.assertIn('did not confirm', cm.exception.summary)
        # An open that timed out may have started Codex, so it is closed too.
        with self.assertRaises(StepFailed) as cm:
            self.run_gen(self.gen(open_error=TimeoutError(), close_reply={}, exits=False))
        self.assertIn('did not confirm', cm.exception.summary)
        # A clean bridge exit confirms it.
        self.assertEqual(self.run_gen(self.gen(after_submit=DONE, close_reply={}, exits=True)), 'A tip calculator.')


class Snapshot(unittest.TestCase):
    """The builder's private copy of the written source: plain files only, bounded, never through a link."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = Path(self.tmp.name) / 'source'
        (self.src / 'res/values').mkdir(parents=True)
        (self.src / 'res/values/strings.xml').write_text('<resources/>')
        self.dest = Path(self.tmp.name) / 'jobs' / 'j' / 'snapshot-1'

    def tearDown(self):
        self.tmp.cleanup()

    def test_copies_plain_files_privately(self):
        from rose.app_builder.service import _snapshot
        _snapshot(self.src, self.dest)
        copy = self.dest / 'res/values/strings.xml'
        self.assertEqual(copy.read_text(), '<resources/>')
        self.assertEqual(copy.stat().st_mode & 0o777, 0o600)
        (self.src / 'res/values/strings.xml').write_text('changed')  # the copy is independent of the source
        self.assertEqual(copy.read_text(), '<resources/>')

    def test_refuses_links_special_files_and_size(self):
        from rose.app_builder import service
        outside = Path(self.tmp.name) / 'secret'
        outside.write_text('key')
        cases = [lambda: (self.src / 'res/raw').symlink_to(outside.parent),
                 lambda: (self.src / 'res/values/k.txt').symlink_to(outside),
                 lambda: os.link(outside, self.src / 'res/values/k.txt'),
                 lambda: os.mkfifo(self.src / 'res/values/pipe'),
                 lambda: (self.src / 'big.bin').write_bytes(b'x' * (service.template.MAX_FILE + 1))]
        for make in cases:
            with self.subTest(make=make):
                shutil.rmtree(self.src)
                (self.src / 'res/values').mkdir(parents=True)
                make()
                with self.assertRaises(StepFailed) as cm:
                    service._snapshot(self.src, self.dest)
                self.assertEqual(cm.exception.category, 'compile')
                self.assertFalse(any(p.is_file() and p.read_bytes() == b'key' for p in self.dest.rglob('*')))
        # Modes are refused as written (checked on the descriptor), and nesting is bounded.
        for make in (lambda: os.chmod(self.src / 'res/values', 0o500),
                     lambda: os.chmod(self.src / 'res/values/strings.xml', 0o200),
                     lambda: (self.src / ('/'.join(['d'] * (service.SNAPSHOT_MAX_DEPTH + 2)))).mkdir(parents=True)):
            with self.subTest(make=make):
                for p in self.src.rglob('*'):
                    os.chmod(p, 0o700)
                shutil.rmtree(self.src)
                (self.src / 'res/values').mkdir(parents=True)
                (self.src / 'res/values/strings.xml').write_text('<resources/>')
                make()
                with self.assertRaises(StepFailed) as cm:
                    service._snapshot(self.src, self.dest)
                self.assertEqual(cm.exception.category, 'compile')
        for p in self.src.rglob('*'):
            os.chmod(p, 0o700)
        old = service.SNAPSHOT_MAX_ENTRIES
        service.SNAPSHOT_MAX_ENTRIES = 3
        try:
            shutil.rmtree(self.src)
            (self.src / 'a/b/c/d').mkdir(parents=True)
            with self.assertRaises(StepFailed):
                service._snapshot(self.src, self.dest)
        finally:
            service.SNAPSHOT_MAX_ENTRIES = old


FAKE_CODEX = textwrap.dedent('''\
    const fs=require('node:fs'),path=require('node:path');
    if(process.argv.includes('--version')){console.log('codex-cli 0.167.1');process.exit(0);}
    const scenario=process.env.ROSE_TEST_SCENARIO||'normal';
    const out=x=>console.log(JSON.stringify(x));
    require('node:readline').createInterface({input:process.stdin}).on('line',line=>{
     const m=JSON.parse(line);if(m.id===undefined)return;let result={};
     if(m.method==='account/read')result={account:{type:'chatgpt'}};
     if(m.method==='model/list')result={data:[{id:'gpt-6.1-terra',serviceTiers:[{id:'priority'}]}]};
     if(m.method==='config/read')result={config:{mcp_servers:{node_repl:{enabled:false}}}};
     if(m.method==='thread/start'){
      fs.writeFileSync(path.join(process.cwd(),'..','thread-start.json'),JSON.stringify(m.params));
      result={thread:{id:'build'},model:'gpt-6.1-terra',serviceTier:'priority'};
     }
     if(m.method==='mcpServerStatus/list')result={data:[{runtimeStatus:'disabled',tools:{},resources:[],resourceTemplates:[]}]};
     if(m.method==='turn/interrupt'){out({id:m.id,result:{}});
      out({method:'turn/completed',params:{threadId:'build',turn:{id:'t1',status:'interrupted'}}});return;}
     if(m.method==='turn/start'){
      out({id:m.id,result:{turn:{id:'t1'}}});
      out({method:'turn/started',params:{threadId:'build',turn:{id:'t1'}}});
      if(scenario==='hang')return;
      fs.writeFileSync(path.join(process.cwd(),'README.md'),'Tip calculator\\n');
      if(scenario==='notes')fs.writeFileSync(path.join(process.cwd(),'res','values','notes.xml'),
       '<?xml version="1.0" encoding="utf-8"?>\\n<resources><string name="note">Tip</string></resources>\\n');
      if(scenario==='late'){
       // A writer that escapes the process group (setsid) and, once the build has started (the test's "go"
       // marker), adds debuggable to the manifest and a Java file that does not compile.
       const m=path.join(process.cwd(),'AndroidManifest.xml');
       require('node:child_process').spawn('setsid',['sh','-c',
        'i=0; until [ -e "$2" ] || [ $i -ge 300 ]; do sleep 0.05; i=$((i+1)); done; '+
        'sed -i "s/<application/<application android:debuggable=\\"true\\"/" "$0"; '+
        'mkdir -p src/late && echo "not java" > src/late/Broken.java; echo late > "$1"',
        m,path.join(process.cwd(),'res','values','late.txt'),path.join(process.cwd(),'..','go')],
        {detached:true,stdio:'ignore',cwd:process.cwd()}).unref();
      }
      if(scenario==='identity'){const f=path.join(process.cwd(),'AndroidManifest.xml');
       fs.writeFileSync(f,fs.readFileSync(f,'utf8').replace('<application','<application android:debuggable="true"'));}
      out({method:'item/completed',params:{threadId:'build',turnId:'t1',item:{type:'agentMessage',text:'A tip calculator.',phase:'final_answer'}}});
      out({method:'turn/completed',params:{threadId:'build',turn:{id:'t1',status:'completed'}}});
      return;
     }
     out({id:m.id,result});
    });
''')


@unittest.skipUnless(shutil.which(os.environ.get('ROSE_NODE_BIN', 'node')), 'needs node for the real bridge')
class RealBridge(unittest.TestCase):
    """CodexClient -> codex-voice-bridge.mjs -> a fake app-server: the same path a host would use."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.source = root / 'projects' / 'p1' / 'source'
        self.source.mkdir(parents=True)
        cli = root / 'codex.cjs'
        cli.write_text(f'#!{shutil.which("node")}\n' + FAKE_CODEX)
        cli.chmod(0o700)
        self.old = {k: os.environ.get(k) for k in ('PIXEL_CODEX_BIN', 'ROSE_TEST_SCENARIO')}
        os.environ['PIXEL_CODEX_BIN'] = str(cli)

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_writes_in_the_project_and_returns_after_the_writer_exits(self):
        os.environ['ROSE_TEST_SCENARIO'] = 'normal'
        note = CodexGenerator(timeout_s=20)(self.source, 'create', 'Build a tip calculator.', lambda: False)
        self.assertEqual(note, 'A tip calculator.')
        self.assertEqual((self.source / 'README.md').read_text(), 'Tip calculator\n')
        import json
        start = json.loads((self.source.parent / 'thread-start.json').read_text())
        self.assertEqual((start['cwd'], start['sandbox'], start['ephemeral']), (str(self.source.resolve()),
                                                                                'workspace-write', True))
        self.assertIn('Java 8 source level', start['developerInstructions'])
        self.assertFalse(start['config']['sandbox_workspace_write.network_access'])

    def test_cancel_interrupts_and_confirms_the_stop(self):
        os.environ['ROSE_TEST_SCENARIO'] = 'hang'
        stop = threading.Event()
        threading.Timer(1.0, stop.set).start()
        with self.assertRaises(StepFailed) as cm:
            CodexGenerator(timeout_s=20)(self.source, 'revise', 'Add a dark theme.', stop.is_set)
        self.assertEqual(cm.exception.category, 'cancelled')
        self.assertFalse((self.source / 'README.md').exists())


@unittest.skipUnless((HAS_SDK or os.environ.get('ROSE_REQUIRE_APP_BUILD')) and shutil.which('node'),
                     'needs the pinned Android SDK, a JDK and node')
class BuilderWithWriter(RealBridge):
    """Builder + CodexGenerator + real bridge + fake app-server: compilation and signature verification only."""

    def builder(self):
        tools = toolchain.Tools(SDK, JAVA_HOME, timeout_s=300, key_creation=True)  # throwaway keys in a temp dir
        b = Builder(Path(self.tmp.name) / 'builder', tools, CodexGenerator(timeout_s=60), backup=fake_backup)
        self.addCleanup(b.ledger.close)
        return b

    def test_written_app_is_built_and_signed(self):
        os.environ['ROSE_TEST_SCENARIO'] = 'notes'
        b = self.builder()
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r1', 'description': 'Tip calculator'})
        self.assertTrue(b.run_next())
        job = b.job(out['jobId'])
        self.assertEqual(job['state'], 'ready', job)
        source = Path(self.tmp.name) / 'builder' / 'projects' / out['projectId'] / 'source'
        self.assertTrue((source / 'res/values/notes.xml').is_file())
        self.assertEqual(b.projects()[0]['versions'][0]['versionCode'], 1)
        self.assertEqual(b.projects()[0]['versions'][0]['notes'], 'A tip calculator.')  # the writer's own summary

    @unittest.skipUnless(shutil.which('setsid'), 'needs setsid to start an escaping writer')
    def test_a_writer_left_running_never_changes_what_is_built(self):
        os.environ['ROSE_TEST_SCENARIO'] = 'late'
        b = self.builder()
        real_build = toolchain.build

        def build_after_the_late_write(source, *args, **kwargs):
            # Deterministic order: the escaped writer changes the live source after the snapshot and before any
            # build step reads files. Building from the live source would then fail (debuggable, broken Java).
            project = Path(self.tmp.name) / 'builder' / 'projects' / out['projectId']
            (project / 'go').touch()
            for _ in range(300):
                if (project / 'source/res/values/late.txt').exists():
                    break
                time.sleep(0.05)
            self.assertTrue((project / 'source/res/values/late.txt').exists())
            return real_build(source, *args, **kwargs)

        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r1', 'description': 'Tip calculator'})
        with unittest.mock.patch.object(toolchain, 'build', build_after_the_late_write):
            self.assertTrue(b.run_next())
        job = b.job(out['jobId'])
        self.assertEqual(job['state'], 'ready', job)
        project = Path(self.tmp.name) / 'builder' / 'projects' / out['projectId']
        source = project / 'source'
        self.assertIn('debuggable', (source / 'AndroidManifest.xml').read_text())  # the live source did change
        self.assertTrue((source / 'src/late/Broken.java').exists())
        # ... but the recorded revision and the delivered APK are the checked snapshot.
        from rose.app_builder.service import _git
        version = b.projects()[0]['versions'][0]
        manifest = _git(source, 'show', version['sourceRevision'] + ':AndroidManifest.xml')
        self.assertNotIn('debuggable', manifest)
        self.assertNotIn('late.txt', _git(source, 'ls-tree', '-r', '--name-only', version['sourceRevision']))
        apk = b.artifact(version['apk']['sha256'])
        aapt = toolchain.Tools(SDK).bt / 'aapt'
        badging = subprocess.run([str(aapt), 'dump', 'xmltree', str(apk), 'AndroidManifest.xml'],
                                 capture_output=True, text=True, check=True).stdout
        self.assertNotIn('debuggable', badging)
        # The next job starts again from the delivered revision, not from what was left in the source.
        os.environ['ROSE_TEST_SCENARIO'] = 'notes'
        rev = b.revise('d', out['projectId'], {'schemaVersion': 1, 'clientRequestId': 'r2', 'request': 'again'})
        self.assertTrue(b.run_next())
        self.assertEqual(b.job(rev['jobId'])['state'], 'ready', b.job(rev['jobId']))
        self.assertNotIn('debuggable', _git(source, 'show', b.projects()[0]['versions'][1]['sourceRevision']
                                            + ':AndroidManifest.xml'))

    def test_writer_identity_change_is_refused_by_the_builder(self):
        os.environ['ROSE_TEST_SCENARIO'] = 'identity'
        b = self.builder()
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r1', 'description': 'Tip calculator'})
        b.run_next()
        job = b.job(out['jobId'])
        self.assertEqual((job['state'], job['failure']['category']), ('failed', 'compile'), job)
        self.assertEqual(b.projects()[0]['versions'], [])


if __name__ == '__main__':
    unittest.main()
