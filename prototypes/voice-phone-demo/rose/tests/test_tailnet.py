"""Offline address-policy and no-fallback regressions; no real tailnet or pairing credentials."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rose.remote import Remote, configured_hosts, discover_hosts, tailscale_host, tailscale_self


class Tailnet(unittest.TestCase):
    def test_configured_hosts_and_boundary_addresses(self):
        self.assertEqual(configured_hosts(['Rose.tail1.ts.net', '100.64.0.0', '100.127.255.255', '100.64.0.0']),
                         ['rose.tail1.ts.net', '100.64.0.0', '100.127.255.255'])
        for host in ('192.168.1.2', '10.0.0.1', '172.16.1.2', '127.0.0.1', 'localhost', 'mac.local',
                     'example.com', '100.63.255.255', '100.128.0.0', 'evil.ts.net.attacker.com', '.ts.net',
                     'http://mac.ts.net', 'user@mac.ts.net', 'mac.ts.net:8443', 'mac..ts.net', '100.064.0.1'):
            with self.subTest(host=host):
                self.assertFalse(tailscale_host(host))
                with self.assertRaises(ValueError):
                    configured_hosts([host])

    def test_discovery_filters_bad_client_addresses_and_never_scans_lan(self):
        with patch('rose.remote.tailscale_self', return_value=('mac.tail1.ts.net', ['100.64.0.2', '192.168.1.2', '100.64.0.2'])):
            self.assertEqual(discover_hosts(), ['mac.tail1.ts.net', '100.64.0.2'])
        with patch('rose.remote.tailscale_self', return_value=(None, [])):
            self.assertEqual(discover_hosts(), [])

    def test_client_must_be_running_and_logged_in(self):
        for state in ('Stopped', 'NeedsLogin', 'Starting', None):
            response = {'BackendState': state, 'Self': {'DNSName': 'mac.tail1.ts.net.', 'TailscaleIPs': ['100.64.0.2']}}
            with patch('rose.remote._tailscale_cli', return_value='/test/tailscale'), patch('rose.remote.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(response))):
                self.assertEqual(tailscale_self(), (None, []))
        response = {'BackendState': 'Running', 'Self': {'DNSName': 'mac.tail1.ts.net.', 'TailscaleIPs': ['100.64.0.2', '192.168.1.2']}}
        with patch('rose.remote._tailscale_cli', return_value='/test/tailscale'), patch('rose.remote.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(response))):
            self.assertEqual(tailscale_self(), ('mac.tail1.ts.net', ['100.64.0.2']))

    def test_bad_or_missing_addresses_do_not_mint_a_pairing_credential(self):
        remote = object.__new__(Remote)
        remote.pairing = Mock()
        for hosts in ([], ['192.168.1.2'], ['100.64.0.2', '192.168.1.2'], ['127.0.0.1']):
            remote.discover = lambda: hosts
            with self.assertRaises(LookupError):
                remote.new_invite()
            remote.pairing.new.assert_not_called()

    def test_unavailable_remote_listener_never_enables_usb_fallback(self):
        from fastapi.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect
        from rose.app import build_app
        server = SimpleNamespace(args=SimpleNamespace(port=8091), serve_phone=Mock())
        for listening in (False, True):
            client = TestClient(build_app(server, SimpleNamespace(listening=listening)),
                                base_url='http://127.0.0.1:8091')
            with client.websocket_connect('ws://127.0.0.1:8091/ws/phone') as ws:
                with self.assertRaises(WebSocketDisconnect) as refused:
                    ws.receive_text()
                self.assertEqual(refused.exception.code, 4009)
            server.serve_phone.assert_not_called()


if __name__ == '__main__':
    unittest.main()
