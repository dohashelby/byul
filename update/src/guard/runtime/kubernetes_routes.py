"""Selected trusted/untrusted Pod origins and one owned Gateway Pod.

Reuse the existing exact engine/ENV mapping, kubectl capture and frozen stdlib
probe. This profile does not discover Services, TLS, host routes or principals.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

from guard.models import canonical_json, sha256_hex
from .kubernetes_binding import validate_container_binding, verify_owned_pod
from .kubernetes_http import (KubernetesHTTP, KubernetesBindingError,
                              REQUEST_BINDING_PROFILE, PROBE_SOURCE_SHA256)

PROFILE = 'kubernetes-owned-routes-http-v1'
EXTERNAL = 'kubernetes-external-probe'


def _endpoint(value, binding):
    u = urlsplit(value)
    if (u.scheme != 'http' or u.hostname != binding['pod_ip'] or not u.port
            or u.username is not None or u.password is not None or u.path or u.query or u.fragment):
        raise KubernetesBindingError()
    return u


class KubernetesRoutesHTTP:
    profile = PROFILE

    def __init__(self, target, env, *, duration=300, binary='kubectl', runner=None):
        self.base = KubernetesHTTP(target, env, duration=duration, binary=binary,
                                   runner=runner, profile=REQUEST_BINDING_PROFILE)
        self.api_model, self.model_mapping = self.base.api_model, deepcopy(self.base.model_mapping)
        self.target_sha256 = sha256_hex(canonical_json(target))
        self.injected = self.base.injected
        self.rows, self.failed = [], False
        try:
            extra = target['kubernetes_routes']
            if (type(extra) is not dict or set(extra) != {'external_probe', 'gateway'}
                    or target['origins']['external'] != {'network': EXTERNAL}
                    or set(target['origins']) != {'trusted', 'external'}):
                raise ValueError
            self.binding = {**deepcopy(self.base.binding), **deepcopy(extra)}
            for name in ('external_probe', 'gateway'):
                validate_container_binding(self.binding[name])
            roles = ('engine', 'probe', 'external_probe', 'gateway')
            for key in ('pod_uid', 'container_id', 'pod_ip'):
                if len({self.binding[r][key] for r in roles}) != len(roles):
                    raise ValueError
            self.engine = _endpoint(target['engine']['trusted_url'], self.binding['engine'])
            self.gateway = _endpoint(target['gateway']['url'], self.binding['gateway'])
            direct = target['direct_targets']
            if type(direct) is not list or any(x != {'origin': 'external', 'url': target['engine']['trusted_url']} for x in direct) or len(direct) > 1:
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError):
            raise KubernetesBindingError() from None
        source = (Path(__file__).parent/'assets/http_probe_v1.py.txt').read_text(encoding='utf-8')
        if hashlib.sha256(source.encode()).hexdigest() != PROBE_SOURCE_SHA256:
            raise KubernetesBindingError()
        self.source = ('import math,os,sys,threading\nlimit=float(sys.argv[1])\n'
            'if not math.isfinite(limit) or not 0 < limit <= 300: sys.exit(124)\n'
            'watchdog=threading.Timer(limit,lambda:os._exit(124))\nwatchdog.daemon=True\nwatchdog.start()\n'
            + 'exec(compile('+repr(source)+",'guard-route-http-probe','exec'),{'__name__':'__main__'})\n")
        self.source_sha256 = sha256_hex(self.source.encode())

    def _verify(self):
        self.base._verify(capture_snapshot=True)
        # The common engine/ENV relation is not imposed on the Gateway image.
        # Its independent exact runtime generation is verified with the same
        # owned Pod validator, context and run, before and after each dispatch.
        for role in ('external_probe', 'gateway'):
            b = self.binding[role]
            ns = self.base._call(['get', 'namespace', b['namespace'], '-o', 'json'])
            if ns.get('kind') != 'Namespace':
                raise KubernetesBindingError()
            pod = self.base._call(['--namespace='+b['namespace'], 'get', 'pod', b['pod'], '-o', 'json'])
            if pod.get('kind') != 'Pod':
                raise KubernetesBindingError()
            verify_owned_pod(pod, ns['metadata'], b, self.binding['run_id'])

    def __call__(self, network, requests, timeout):
        ids = []
        try:
            if (self.failed or network not in {'kubernetes-probe', EXTERNAL}
                    or type(requests) is not list or not 1 <= len(requests) <= 64
                    or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120):
                raise KubernetesBindingError()
            for r in requests:
                rid = r['id'];u = urlsplit(r['url'])
                if (type(rid) is not str or re.fullmatch(r'[A-Za-z0-9_./{}:| -]{1,512}', rid) is None
                        or rid in ids or u.query or u.fragment or u.username is not None or u.password is not None
                        or u.scheme != 'http' or u.netloc not in {self.engine.netloc, self.gateway.netloc}
                        or r.get('method') not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'TCP'}
                        or network == 'kubernetes-probe' and u.netloc != self.engine.netloc
                        or r.get('mode', 'http') not in {'http', 'tcp'}
                        or (r.get('mode') == 'tcp') != (r.get('method') == 'TCP')):
                    raise KubernetesBindingError()
                ids.append(rid)
            self._verify()
            remaining = self.base.deadline-self.base.clock()
            # Per-request socket timeouts consume the existing whole-run
            # budget. Their exact sum leaves no time for imports/serialization
            # and can kill a complete all-timeout batch before it is returned.
            budget = remaining
            if budget <= 0:
                raise KubernetesBindingError()
            payload = json.dumps({'requests': requests, 'timeout': min(timeout, remaining)},
                                 ensure_ascii=False, allow_nan=False).encode()
            if len(payload) > 65536:
                raise KubernetesBindingError()
            p = self.binding['probe' if network == 'kubernetes-probe' else 'external_probe']
            raw = self.base._call(['--namespace='+p['namespace'], 'exec', '-i', p['pod'],
                '--container='+p['container'], '--', 'python3', '-c', self.source, str(budget)],
                payload=payload, cap=2*1024**2, budget=remaining)
            self._verify()
            if type(raw) is not list or len(raw) != len(ids) or [r.get('id') for r in raw] != ids:
                raise KubernetesBindingError()
            safe = []
            for request, value in zip(requests, raw):
                outcome, phase = value.get('outcome'), value.get('phase')
                if not (type(outcome) is int and 100 <= outcome <= 599 or
                        outcome in {'connected','timeout','refused','unreachable','dns_error'} and phase == 'connect' or
                        outcome == 'error'):
                    outcome = 'probe_unavailable'
                row = {'id': request['id'], 'method': request['method'], 'outcome': outcome,
                       'phase': phase if phase in {'connect','response','after_connect'} else None,
                       'body': value.get('body') if type(value.get('body')) is str else None}
                for key in ('body_bytes','elapsed_ms'):
                    if type(value.get(key)) is int and value[key] >= 0:
                        row[key] = value[key]
                safe.append(row)
            self.rows.append({'request_ids': ids, 'origin': network, 'binding_verified_before_after': True})
            return safe
        except Exception:
            self.failed = True
            self.rows.append({'request_ids': ids, 'binding_verified_before_after': False,
                              'diagnostic': 'KUBERNETES_ROUTE_BINDING_UNAVAILABLE'})
            # Earlier successful batches and independent FAIL observations
            # survive. A changed/failed batch cannot become a blocked route.
            return [{'id': r.get('id'), 'method': r.get('method'), 'outcome': 'probe_unavailable'}
                    for r in requests] if type(requests) is list else []

    def report(self):
        return {'profile': PROFILE, 'target_sha256': self.target_sha256,
                'binding': deepcopy(self.binding), 'probe_source_sha256': self.source_sha256,
                'requests': deepcopy(self.rows), 'collection_gap': self.failed,
                'cleanup_errors': list(self.base.cleanup_errors),
                'scope': 'selected trusted/untrusted Pod origins and explicit Gateway Pod HTTP only',
                'limitations': ['Source labels are operator declarations; no Internet/host/Service/TLS coverage.',
                               'Credentials and principal meanings require explicit operator policy/configuration.',
                               'No deployed Gateway transform/worker handler consumption/GPU proof.']}
