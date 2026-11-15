"""The shared API contract (docs/APP-BUILDER.md §7.3): strict parsing, a canonical digest, and a revision check that
keeps installed older clients working unless the writer raises `api`. Every contract is one the test writes."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from rose.app_builder import contract as C

BASE = {
    'schemaVersion': 1, 'api': 1,
    'types': {
        'Note': {'fields': {'id': 'int', 'text': {'type': 'string', 'max': 100},
                            'tags': {'type': 'list', 'of': 'string', 'max': 10},
                            'parent': {'type': 'Note', 'optional': True},
                            'color': {'type': 'string', 'optional': True}}},
        'NoteList': {'fields': {'notes': {'type': 'list', 'of': 'Note'}}},
        'NewNote': {'fields': {'text': {'type': 'string', 'max': 100}, 'pinned': {'type': 'bool', 'optional': True}}},
        'Empty': {'fields': {}},
    },
    'routes': {
        'listNotes': {'method': 'GET', 'path': '/app/notes', 'response': 'NoteList'},
        'addNote': {'method': 'POST', 'path': '/app/notes', 'request': 'NewNote', 'response': 'Note'},
        'about': {'method': 'GET', 'path': '/app/about', 'auth': 'public', 'response': 'Empty'},
        'ping': {'method': 'POST', 'path': '/app/ping', 'response': 'Empty'},
    },
}


def changed(edit, base=BASE):
    data = copy.deepcopy(base)
    edit(data)
    return data


class Parse(unittest.TestCase):
    def test_a_good_contract_is_canonical(self):
        c = C.parse(BASE)
        self.assertEqual(c.data['types']['Note']['fields']['id'], {'type': 'int', 'optional': False})
        self.assertEqual(c.data['routes']['listNotes']['auth'], 'account')  # the default, spelled out
        self.assertIsNone(c.data['routes']['listNotes']['request'])
        reordered = json.loads(json.dumps(BASE), object_pairs_hook=lambda p: dict(reversed(p)))
        self.assertEqual(C.parse(reordered).sha256, c.sha256)  # key order never changes the digest
        spelled = changed(lambda d: d['types']['Note']['fields'].__setitem__('id', {'type': 'int', 'optional': False}))
        self.assertEqual(C.parse(spelled).sha256, c.sha256)  # nor does spelling out a default
        self.assertNotEqual(C.parse(changed(lambda d: d.__setitem__('api', 2))).sha256, c.sha256)

    def test_malformed_contracts_are_refused(self):
        F = lambda d: d['types']['Note']['fields']  # noqa: E731
        R = lambda d: d['routes']  # noqa: E731
        bad = {
            'unknown top key': lambda d: d.__setitem__('extra', 1),
            'schema 2': lambda d: d.__setitem__('schemaVersion', 2),
            'api 0': lambda d: d.__setitem__('api', 0),
            'api true': lambda d: d.__setitem__('api', True),
            'no routes': lambda d: d.__setitem__('routes', {}),
            'lower-case type': lambda d: d['types'].__setitem__('note', {'fields': {}}),
            'built-in type name': lambda d: d['types'].__setitem__('String', {'fields': {}}),
            'type without fields': lambda d: d['types'].__setitem__('X', {}),
            'unknown field type': lambda d: F(d).__setitem__('x', 'date'),
            'upper-case field': lambda d: F(d).__setitem__('Bad', 'int'),
            'list without of': lambda d: F(d).__setitem__('x', {'type': 'list'}),
            'of on a primitive': lambda d: F(d).__setitem__('x', {'type': 'int', 'of': 'int'}),
            'max on int': lambda d: F(d).__setitem__('x', {'type': 'int', 'max': 3}),
            'max zero': lambda d: F(d).__setitem__('x', {'type': 'string', 'max': 0}),
            'optional inside of': lambda d: F(d).__setitem__('x', {'type': 'list', 'of': {'type': 'int',
                                                                                          'optional': True}}),
            'too deep': lambda d: F(d).__setitem__('x', {'type': 'list', 'of': {'type': 'list', 'of': {
                'type': 'list', 'of': {'type': 'list', 'of': {'type': 'list', 'of': {'type': 'list',
                                                                                     'of': 'int'}}}}}}),
            'unknown field key': lambda d: F(d).__setitem__('x', {'type': 'int', 'default': 1}),
            'bad method': lambda d: R(d)['listNotes'].__setitem__('method', 'PATCH'),
            'path outside /app': lambda d: R(d)['listNotes'].__setitem__('path', '/signin/v1/session'),
            'path with a parameter': lambda d: R(d)['listNotes'].__setitem__('path', '/app/notes/{id}'),
            'bare /app': lambda d: R(d)['listNotes'].__setitem__('path', '/app'),
            'same method and path': lambda d: R(d).__setitem__('again', dict(d['routes']['listNotes'])),
            'bad auth': lambda d: R(d)['listNotes'].__setitem__('auth', 'admin'),
            'unknown response': lambda d: R(d)['listNotes'].__setitem__('response', 'Nope'),
            'no response': lambda d: R(d)['listNotes'].pop('response'),
            'GET with a body': lambda d: R(d)['listNotes'].__setitem__('request', 'NewNote'),
            'upper-case route': lambda d: R(d).__setitem__('ListAll', {'method': 'GET', 'path': '/app/all',
                                                                      'response': 'Empty'}),
        }
        for name, edit in bad.items():
            with self.subTest(name):
                with self.assertRaises(C.ContractRefused) as caught:
                    C.parse(changed(edit))
                self.assertEqual(caught.exception.reason, 'contract_invalid')

    def test_a_type_that_could_only_be_infinite_is_refused(self):
        cyclic = changed(lambda d: d['types'].update({'A': {'fields': {'b': 'B'}}, 'B': {'fields': {'a': 'A'}}}))
        with self.assertRaises(C.ContractRefused) as caught:
            C.parse(cyclic)
        self.assertEqual(caught.exception.reason, 'type_cycle')
        for breaker in ({'type': 'A', 'optional': True}, {'type': 'list', 'of': 'A'}, {'type': 'map', 'of': 'A'}):
            with self.subTest(breaker):
                C.parse(changed(lambda d: d['types'].update({'A': {'fields': {'b': 'B'}},
                                                             'B': {'fields': {'a': breaker}}})))
        self_ref = changed(lambda d: d['types'].update({'S': {'fields': {'me': 'S'}}}))
        with self.assertRaises(C.ContractRefused):
            C.parse(self_ref)

    def test_load_reads_the_file_strictly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'contract').mkdir()
            path = root / C.FILE
            path.write_text(json.dumps(BASE))
            self.assertEqual(C.load(root).sha256, C.parse(BASE).sha256)
            repeated = json.dumps(BASE).replace('"api": 1', '"api": 1, "api": 1').encode()
            for raw in (b'{', b'\xff', repeated, b'[' * 100000):
                with self.subTest(raw[:20]):
                    path.write_bytes(raw)
                    with self.assertRaises(C.ContractRefused):
                        C.load(root)
            path.unlink()
            with self.assertRaises(C.ContractRefused):
                C.load(root)
            path.write_bytes(b' ' * (C.MAX_FILE + 1))
            with self.assertRaises(C.ContractRefused):
                C.load(root)


class Update(unittest.TestCase):
    def check(self, edit, api=None):
        new = changed(edit)
        if api is not None:
            new['api'] = api
        return C.check_update(C.parse(BASE), C.parse(new))

    def breaks(self, edit):
        with self.assertRaises(C.ContractRefused) as caught:
            self.check(edit)
        self.assertEqual(caught.exception.reason, 'breaking_without_major', str(caught.exception))
        update = self.check(edit, api=2)  # the same change, declared
        self.assertTrue(update.breaking and update.client_update_required)
        return update

    def test_additive_changes_pass(self):
        F = lambda d, t: d['types'][t]['fields']  # noqa: E731
        additive = {
            'a new route': lambda d: d['routes'].__setitem__('count', {'method': 'GET', 'path': '/app/count',
                                                                      'response': 'Empty'}),
            'a new type': lambda d: d['types'].__setitem__('Extra', {'fields': {}}),
            'a new response field': lambda d: F(d, 'Note').__setitem__('created', 'int'),
            'an optional response field dropped (non-recursive)': lambda d: F(d, 'Note').pop('color'),
            'a new optional request field': lambda d: F(d, 'NewNote').__setitem__('color',
                                                                                  {'type': 'string',
                                                                                   'optional': True}),
            'a request field dropped (servers ignore extras)': lambda d: F(d, 'NewNote').pop('pinned'),
            'a request field made optional': lambda d: F(d, 'NewNote')['text'].__setitem__('optional', True),
            'a request limit raised': lambda d: F(d, 'NewNote')['text'].__setitem__('max', 1000),
            'a response field made required': lambda d: F(d, 'Note')['color'].__setitem__('optional', False),
            'an optional response field dropped': lambda d: F(d, 'Note').pop('parent'),
            'a type renamed, same shape': lambda d: (d['types'].__setitem__('Notes', d['types'].pop('NoteList')),
                                                    d['routes']['listNotes'].__setitem__('response', 'Notes')),
            'account route made public': lambda d: d['routes']['listNotes'].__setitem__('auth', 'public'),
            'a POST route gains an all-optional body': lambda d: (
                d['types'].__setitem__('Opt', {'fields': {'x': {'type': 'int', 'optional': True}}}),
                d['routes']['ping'].__setitem__('request', 'Opt')),
            'a body-less route gains an optional body': lambda d: (
                d['routes']['about'].__setitem__('method', 'POST'), None) and None,
        }
        additive.pop('a body-less route gains an optional body')  # a method change: covered as breaking below
        for name, edit in additive.items():
            with self.subTest(name):
                update = self.check(edit)
                self.assertEqual((update.breaking, update.client_update_required), ((), False))

    def test_breaking_changes_need_api_raised(self):
        F = lambda d, t: d['types'][t]['fields']  # noqa: E731
        breaking = {
            'a route removed': lambda d: d['routes'].pop('about'),
            'a route moved': lambda d: d['routes']['about'].__setitem__('path', '/app/info'),
            'a method changed': lambda d: d['routes']['addNote'].__setitem__('method', 'PUT'),
            'public route now needs sign-in': lambda d: d['routes']['about'].__setitem__('auth', 'account'),
            'a response field removed': lambda d: F(d, 'Note').pop('text'),
            'a response field made optional': lambda d: F(d, 'Note')['text'].__setitem__('optional', True),
            'a response field retyped': lambda d: F(d, 'Note')['id'].__setitem__('type', 'string')
            if isinstance(F(d, 'Note')['id'], dict) else F(d, 'Note').__setitem__('id', 'string'),
            'a list entry retyped': lambda d: F(d, 'Note').__setitem__('tags', {'type': 'list', 'of': 'int'}),
            'a nested response field removed': lambda d: F(d, 'Note').pop('id'),
            'a new required request field': lambda d: F(d, 'NewNote').__setitem__('color', 'string'),
            'a request field made required': lambda d: F(d, 'NewNote')['pinned'].__setitem__('optional', False),
            'a request limit lowered': lambda d: F(d, 'NewNote')['text'].__setitem__('max', 10),
            'a request limit added': lambda d: F(d, 'NewNote')['pinned'].__setitem__('type', 'string') or
            F(d, 'NewNote')['pinned'].__setitem__('max', 5),
            'a POST route now needs a body': lambda d: d['routes']['ping'].__setitem__('request', 'NewNote'),
            'a route now needs a body': lambda d: d['routes'].__setitem__(
                'about', {'method': 'POST', 'path': '/app/about', 'auth': 'public', 'request': 'NewNote',
                          'response': 'Empty'}),
        }
        for name, edit in breaking.items():
            with self.subTest(name):
                self.breaks(edit)

    def test_nested_and_recursive_types_are_compared_through(self):
        update = self.breaks(lambda d: d['types']['Note']['fields']['parent'].__setitem__('type', 'NoteList'))
        self.assertTrue(any('parent' in b for b in update.breaking), update.breaking)
        # A recursive type compared with itself terminates and finds nothing.
        self.assertEqual(C.compare(C.parse(BASE), C.parse(BASE)), [])

    def test_api_can_rise_without_a_break_but_never_fall(self):
        update = self.check(lambda d: None, api=3)
        self.assertEqual((update.breaking, update.client_update_required), ((), True))
        with self.assertRaises(C.ContractRefused) as caught:
            C.check_update(C.parse(changed(lambda d: d.__setitem__('api', 2))), C.parse(BASE))
        self.assertEqual(caught.exception.reason, 'api_lowered')

    def test_the_refusal_says_what_breaks_and_what_to_do(self):
        with self.assertRaises(C.ContractRefused) as caught:
            self.check(lambda d: d['routes'].pop('about'))
        self.assertIn('route about is removed', str(caught.exception))
        self.assertIn('Raise api to 2', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
