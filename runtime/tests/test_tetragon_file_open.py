import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

from serving_runtime.enforcement.attribution import KERNEL_DATA
from serving_runtime.enforcement.events import parse_tetragon, UnsupportedTetragonEvent
from serving_runtime.enforcement.intent import parse_intent
from serving_runtime.enforcement.tetragon import comparable_spec, render_manifests
from serving_runtime.enforcement.verification import match_probe_events, reassess_record
from serving_runtime.operations.safety import is_mode_switch_target, requires_restart_confirmation
from tools.tetragon_capability import classify_capabilities
from tools.tetragon_status import method_from_spec
from tools.tetragon_trial import METHODS


def policy(method):
    root = Path(__file__).resolve().parents[1]
    source = json.loads((root / 'deploy/lab/intent.lab-v1.1.json').read_text())
    source.update(tetragon_enforce_method=method, tetragon_kernel_identity=True,
                  tetragon_rule_modes={'TG-SECRET-01': 'enforce'})
    source['protected_paths'].update(secret_paths=['/run/probe/token'], enforce_exact_files=['/run/probe/token'])
    if method == 'override_syscall':
        source['tetragon_openat_symbol'] = '__x64_sys_openat'
    return render_manifests(parse_intent(source))['tetragon-tg-secret-01.json']


class FileOpenTests(unittest.TestCase):
    def test_new_policies_keep_process_alive_and_identity_budget(self):
        for method in ('lsm_file_open', 'override_syscall'):
            with self.subTest(method=method):
                manifest = policy(method)
                spec = manifest['spec']
                probe = spec.get('lsmhooks', spec.get('kprobes'))[0]
                self.assertEqual(probe['selectors'][0]['matchActions'],
                                 [{'action': 'Post'}, {'action': 'Override', 'argError': -13}])
                self.assertLessEqual(len(probe['args']) + len(probe.get('data', [])), 5)
                self.assertEqual(spec['options'], [{'name': 'policy-mode', 'value': 'monitor'}])
                self.assertEqual(method_from_spec(manifest), method)
                self.assertIn(method, METHODS)
                self.assertTrue(is_mode_switch_target({'mode': 'monitor', 'method': method}))
                if method == 'lsm_file_open':
                    self.assertNotIn('kprobes', spec)
                    self.assertEqual(probe['hook'], 'file_open')
                    self.assertEqual(probe['args'], [{'index': 0, 'type': 'file'}])
                    self.assertNotIn('data', probe)
                    self.assertNotIn('serving-runtime.local/kernel-identity', manifest['metadata']['annotations'])
                else:
                    self.assertEqual(probe['data'], KERNEL_DATA)
                    self.assertEqual(probe['selectors'][0]['matchArgs'][0]['values'], ['/run/probe/token\x00'])
                    # Existing deployed syscall+Sigkill policies still identify as syscall.
                    probe['selectors'][0]['matchActions'].append({'action': 'Sigkill'})
                    self.assertEqual(method_from_spec(manifest), method)
                defaulted = copy.deepcopy(spec)
                defaulted.get('lsmhooks', defaulted.get('kprobes'))[0]['args'][0]['resolve'] = ''
                self.assertEqual(comparable_spec(defaulted), comparable_spec(spec))

    def test_lsm_capability_precedes_syscall_and_missing_is_unknown(self):
        common = dict(kernel='7.0.0-test', config='CONFIG_BPF_KPROBE_OVERRIDE=y\nCONFIG_BPF_LSM=y',
                      injection_list='__x64_sys_openat')
        for lsm, probe in [('lockdown,bpf\n', 'override_return: true\nlsm: false'),
                           (None, 'override_return: true\nlsm: true')]:
            result = classify_capabilities(**common, lsm=lsm, probe=probe)
            self.assertIn('lsm_file_open', result['available'])
            self.assertEqual(result['recommended'], 'lsm_file_open')
        result = classify_capabilities(**common, lsm='apparmor', probe='override_return: true\nlsm: false')
        self.assertIn('lsm_file_open', result['unavailable'])
        self.assertEqual(result['recommended'], 'override_syscall')
        result = classify_capabilities(kernel=None, config=None, lsm=None, injection_list=None, probe=None)
        self.assertIn('lsm_file_open', result['unknown'])
        self.assertIsNone(result['recommended'])

    def test_lsm_events_and_errno_require_bounded_direct_evidence(self):
        at = datetime(2026, 11, 8, tzinfo=timezone.utc)
        raw = {'time': (at + timedelta(seconds=1)).isoformat(), 'process_lsm': {
            'function_name': 'file_open', 'policy_name': 'runtime-tg-secret-01',
            'process': {'pid': 42, 'binary': '/usr/bin/python3', 'pod': {
                'namespace': 'test', 'name': 'probe', 'uid': 'uid', 'container': {'pid': 1}}},
            'args': [{'file_arg': {'path': '/run/probe/token'}}], 'action': 'KPROBE_ACTION_OVERRIDE'}}
        event = parse_tetragon(raw, data_origin='synthetic')
        self.assertEqual((event['access'], event['action_mode']), ('open', 'enforce'))
        params = dict(policy='runtime-tg-secret-01', namespace='test', pod_uid='uid', path='/run/probe/token',
                      since=at.isoformat(), until=(at + timedelta(seconds=4)).isoformat(), main_process=True, pid=42)
        events = match_probe_events([json.dumps(raw)], **params)
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0]['return_value'])
        record = dict(method='lsm_file_open', namespace='test', policy=params['policy'], pod_uid='uid',
                      probe_path=params['path'], main_pid=42, policy_id='7', events=events,
                      started_at=params['since'], finished_at=params['until'],
                      policy_actions=[{'action': 'Override', 'argError': -13}],
                      read_result={'read': 'denied', 'errno': 1},
                      facts=dict.fromkeys(('main_process', 'collector_complete', 'monitor_read_ok',
                                           'restore_read_ok', 'cleanup_ok'), True),
                      steps=[{'at': at.isoformat(), 'target_mode': 'enforce', 'ok': True,
                              'before': {'mode': 'monitor', 'id': '7'},
                              'after': {'mode': 'enforce', 'id': '7', 'state': 'enabled'}},
                             {'at': (at + timedelta(seconds=3)).isoformat(), 'target_mode': 'monitor', 'ok': True,
                              'before': {'mode': 'enforce', 'id': '7'},
                              'after': {'mode': 'monitor', 'id': '7', 'state': 'enabled'}}])
        for method in ('lsm_file_open', 'override_syscall'):
            for errno in (1, 13, 2):
                method_events = events if method == 'lsm_file_open' else [{**events[0], 'function': 'sys_openat'}]
                result = reassess_record({**record, 'method': method, 'events': method_events,
                                          'read_result': {'read': 'denied', 'errno': errno}})
                expected = errno == 13 or (errno == 1 and method == 'lsm_file_open')
                self.assertEqual(result['verification']['status'], 'passed' if expected else 'unknown')
                self.assertFalse(result['facts']['kernel_read_denied'])
                self.assertEqual(result['verification']['process_termination'], 'unknown')
        for changes in ({'events': []}, {'policy_actions': []}, {'pod_uid': 'other'},
                        {'facts': {**record['facts'], 'collector_complete': False}}):
            self.assertEqual(reassess_record({**record, **changes})['verification']['status'], 'unknown')
        self.assertEqual(match_probe_events([json.dumps(raw)], **{**params, 'pod_uid': 'other'}), [])
        bad = copy.deepcopy(raw)
        bad['process_lsm']['function_name'] = 'other_hook'
        with self.assertRaises(UnsupportedTetragonEvent):
            parse_tetragon(bad, data_origin='synthetic')

    def test_confirmation_uses_observed_actions_and_keeps_legacy_conservative(self):
        for method in METHODS:
            self.assertFalse(requires_restart_confirmation({'method': method, 'contains_sigkill': False}))
            self.assertTrue(requires_restart_confirmation({'method': method, 'contains_sigkill': True}))
        self.assertTrue(requires_restart_confirmation({'method': 'override_syscall'}))
        self.assertFalse(requires_restart_confirmation({'method': 'lsm_file_open'}))
