"""Standalone Level 2 composition, with synthetic owner facilities and a fake writer transport.

The actual host, codegen and job path run; server compilation is intercepted.
Nothing executes cargo, bubblewrap, a model, a signer, a listener or a phone.
Synthetic toolchain digests here are not production pins or Linux acceptance.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rose.app_builder import codegen, crates, generator, host, server_build, toolchain
from rose.app_builder.providers.host import BubblewrapIsolation, PinnedRustTools, toolchain_digest
from rose.tests.test_app_generator import DONE, FakeClient
from rose.tests.test_app_host import config_data
from rose.tests.test_app_level2_pipeline import client_server_app
from rose.tests.test_app_server_build import crate_bytes


class Composition(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.rust = self.root / 'rust'
        (self.rust / 'bin').mkdir(parents=True)
        (self.rust / 'lib/rustlib' / server_build.TARGET / 'lib').mkdir(parents=True)
        for name in ('cargo', 'rustc'):
            file = self.rust / 'bin' / name
            file.write_text('#!/bin/sh\nexit 99\n')  # must never be invoked
            file.chmod(0o755)
        self.pin = toolchain_digest(self.rust)
        self.bwrap = self.root / 'bwrap'
        self.bwrap.write_text('#!/bin/sh\nexit 98\n')
        self.bwrap.chmod(0o755)
        self.store = self.root / 'crates'
        self.store.mkdir()
        (self.store / 'index.json').write_text(json.dumps({'schemaVersion': 1, 'crates': {}}))
        self.runtime = self.root / 'runtime'
        for name in (*server_build.BUILDER_CRATES, 'rose-signin'):
            folder = self.runtime / name
            (folder / 'src').mkdir(parents=True)
            (folder / 'Cargo.toml').write_text(f'[package]\nname="{name}"\nversion="0.1.0"\n')
            (folder / 'src/lib.rs').write_text('// synthetic; never compiled\n')

    def level2(self, **extra):
        return {'rustToolchain': str(self.rust), 'rustToolchainSha256': self.pin,
                'bubblewrap': str(self.bwrap), 'crateStore': str(self.store),
                'runtimeRoot': str(self.runtime), **extra}

    def config(self, **extra):
        return host.parse_config(config_data(str(self.root), writer={'argv': ['/not-invoked/test-bridge']},
                                             clientServer=self.level2(), **extra))

    def build_host(self, config):
        built = host.build_host(config)
        self.addCleanup(built.builder.ledger.close)
        return built

    def test_omission_and_null_preserve_level1_defaults(self):
        for number, extra in enumerate(({}, {'clientServer': None})):
            config = host.parse_config(config_data(str(self.root / str(number)),
                                       writer={'argv': ['/not-invoked/test-bridge']}, **extra))
            built = self.build_host(config)
            b = built.builder
            self.assertIsNone(config.client_server)
            self.assertFalse(b.generate.client_server)
            self.assertEqual((b.codegen, b.rust_tools, b.server_isolation, b.crate_source), (None,) * 4)
            self.assertEqual((b.server_crates, b.server_crate_deps), ({}, {}))
            self.assertIs(b.server_url.__self__, b.ledger)  # #861 wiring remains intact

    def test_explicit_configuration_supplies_the_existing_facilities(self):
        data = crate_bytes('fixture-crate', '1.0.0')
        digest = hashlib.sha256(data).hexdigest()
        (self.store / f'{digest}.crate').write_bytes(data)
        (self.store / 'index.json').write_text(json.dumps({'schemaVersion': 1,
                                                        'crates': {'fixture-crate@1.0.0': digest}}))
        config = self.config(buildTimeoutS=321)
        with mock.patch('subprocess.Popen', side_effect=AssertionError('no tool execution during composition')):
            built = self.build_host(config)
        b = built.builder
        self.assertTrue(b.generate.client_server)
        self.assertIs(b.codegen, codegen)
        self.assertIsInstance(b.rust_tools, PinnedRustTools)
        self.assertEqual((b.rust_tools.toolchain, b.rust_tools.expected_sha256, b.rust_tools.target,
                          b.rust_tools.timeout_s), (self.rust, self.pin, server_build.TARGET, 321))
        self.assertIsInstance(b.server_isolation, BubblewrapIsolation)
        self.assertEqual(b.server_isolation.bwrap, self.bwrap)
        self.assertIsInstance(b.crate_source, crates.RecordedCrates)
        self.assertEqual(b.crate_source.root, self.store)
        self.assertEqual([(c.key, c.sha256) for c in b.crate_source.crates()], [('fixture-crate@1.0.0', digest)])
        self.assertEqual(b.server_crates, {n: self.runtime / n for n in server_build.BUILDER_CRATES})
        self.assertEqual(b.server_crate_deps, {'rose-signin': self.runtime / 'rose-signin'})
        self.assertIsNone(built.worker)

    def test_complete_host_writer_prompt_and_job_use_configured_facilities(self):
        clients = []
        def connect(on_event, command):
            self.assertEqual(command, ['/not-invoked/test-bridge'])
            client = FakeClient(on_event, after_submit=DONE)
            request = client.request
            def write(op, session, **params):
                if op == 'submit':
                    source = Path(client.calls[0][2]['cwd'])
                    client_server_app()(source, 'create', 'synthetic notes', lambda: False)
                return request(op, session, **params)
            client.request = write
            clients.append(client)
            return client
        with mock.patch('rose.codex_client.CodexClient', side_effect=connect):
            built = self.build_host(self.config())
        b = built.builder
        seen = []
        def stop(snapshot, work, declared, source, tools, isolation, cancelled, **kwargs):
            self.assertIs(source, b.crate_source)
            self.assertIs(tools, b.rust_tools)
            self.assertIs(isolation, b.server_isolation)
            self.assertEqual(kwargs['path_crates'], b.server_crates)
            self.assertEqual(kwargs['dependent_path_crates'], b.server_crate_deps)
            self.assertTrue({'rose/main.rs', 'rose/api.rs', 'rose/routes.rs', 'rose/wire.rs'}
                            <= set(kwargs['generated']))
            client = Path(work).parent / 'client'
            url = next(client.glob('src/**/rose/RoseServer.java')).read_text()
            self.assertIn('static final String URL = null;', url)  # no real or synthetic binding needed
            self.assertTrue(next(client.glob('src/**/rose/RoseApi.java')).is_file())
            self.assertTrue(next(client.glob('src/**/rose/RoseSigninCore.java')).is_file())
            seen.append(work)
            raise server_build.BuildFailed('toolchain', 'server_build', 'Stopped before compilation by the test.')
        with mock.patch.object(server_build, 'build', side_effect=stop), \
                mock.patch.object(toolchain, 'build') as compile_client, \
                mock.patch.object(toolchain, 'ensure_key') as signer:
            job = b.create('fixture', {'schemaVersion': 1, 'clientRequestId': 'level2',
                                       'description': 'Synthetic notes app'})
            self.assertTrue(b.run_next())
            compile_client.assert_not_called()
            signer.assert_not_called()
        self.assertEqual(len(seen), 1, b.job(job['jobId']))
        self.assertIn(generator.CLIENT_SERVER.strip(), clients[0].calls[1][2]['text'])
        self.assertTrue(clients[0].closed)
        self.assertEqual(b.job(job['jobId'])['failure']['summary'], 'Stopped before compilation by the test.')
        self.assertEqual(b.ledger.versions(job['projectId']), [])

    def test_partial_wrong_or_implicit_config_is_refused(self):
        good = self.level2()
        values = [True, False, '', [], {}, {**good, 'extra': 1},
                  *({k: v for k, v in good.items() if k != missing} for missing in good),
                  *(self.level2(rustToolchainSha256=bad) for bad in ('', 'A' * 64, 'x', None, 5)),
                  *(self.level2(**{key: bad}) for key in ('rustToolchain', 'bubblewrap', 'crateStore', 'runtimeRoot')
                    for bad in ('relative', '', True, []))]
        for value in values:
            with self.subTest(value=value), self.assertRaises(host.ConfigError):
                host.parse_config(config_data(str(self.root), clientServer=value))

    def assert_before_construction(self, config):
        with mock.patch('rose.remote.Remote') as remote, mock.patch.object(generator, 'CodexGenerator') as writer:
            with self.assertRaises(host.ConfigError):
                host.build_host(config)
            writer.assert_not_called()
            remote.assert_not_called()
        self.assertFalse((self.root / 'builder').exists())

    def test_bad_pin_missing_or_linked_facilities_fail_before_writer_and_ledger(self):
        self.assert_before_construction(host.parse_config(config_data(str(self.root),
                                       clientServer=self.level2(rustToolchainSha256='f' * 64))))
        for key in ('rustToolchain', 'bubblewrap', 'crateStore', 'runtimeRoot'):
            with self.subTest(key=key):
                self.assert_before_construction(host.parse_config(config_data(str(self.root),
                                               clientServer=self.level2(**{key: str(self.root / 'missing')}))))
        alias = self.root / 'alias'
        alias.symlink_to(self.runtime, target_is_directory=True)
        self.assert_before_construction(host.parse_config(config_data(str(self.root),
                                       clientServer=self.level2(runtimeRoot=str(alias)))))
        self.bwrap.chmod(0o644)
        self.assert_before_construction(self.config())
        self.bwrap.chmod(0o755)
        (self.rust / 'bin/rustc').write_text('changed after the externally supplied fixture pin')
        self.assert_before_construction(self.config())

    def test_broken_configured_crate_store_or_runtime_never_falls_back(self):
        index = self.store / 'index.json'
        for text in ('{', '[]', '{}', '{"schemaVersion":1,"crates":{"invalid":"bad"}}',
                     json.dumps({'schemaVersion': 1, 'crates': {'absent@1.0.0': 'a' * 64}})):
            index.write_text(text)
            self.assert_before_construction(self.config())
        index.unlink()
        self.assert_before_construction(self.config())
        index.write_text(json.dumps({'schemaVersion': 1, 'crates': {}}))
        (self.runtime / 'rose-signin/src/lib.rs').unlink()
        self.assert_before_construction(self.config())

    def test_opt_in_without_writer_preserves_no_writer_failure(self):
        built = self.build_host(host.parse_config(config_data(str(self.root), clientServer=self.level2())))
        self.assertIs(built.builder.generate, host.no_writer)
        with self.assertRaises(toolchain.StepFailed):
            built.builder.generate(None, None, None, None)

    def test_agent_host_is_off_unless_configured_and_only_with_client_server(self):
        from rose.app_builder import app_host
        built = self.build_host(self.config())
        self.assertIsNone(built.builder.hosting)
        agent = {'chariot': '/usr/local/bin/chariot', 'home': '/srv/chariot-cli', 'agents': ['rose-app-1']}
        config = self.config(agentHost=agent)
        self.assertEqual(config.agent_host, app_host.AgentHostConfig(
            Path('/usr/local/bin/chariot'), Path('/srv/chariot-cli'), ('rose-app-1',), False, app_host.APP_PORT))
        hosting = self.build_host(config).builder.hosting
        self.assertIsInstance(hosting, app_host.AgentHosting)
        bad = [{**agent, 'extra': 1}, {'chariot': agent['chariot'], 'home': agent['home']},
               {**agent, 'chariot': 'chariot'}, {**agent, 'agents': []}, {**agent, 'agents': ['a', 'a']},
               {**agent, 'agents': ['bad name']}, {**agent, 'generateKeyring': 'yes'}, {**agent, 'port': 8088},
               {**agent, 'port': 42617}, {**agent, 'port': 80}, {**agent, 'port': True}]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(host.ConfigError):
                self.config(agentHost=data)
        with self.assertRaisesRegex(host.ConfigError, 'needs clientServer'):
            host.parse_config(config_data(str(self.root), agentHost=agent))


if __name__ == '__main__':
    unittest.main()
