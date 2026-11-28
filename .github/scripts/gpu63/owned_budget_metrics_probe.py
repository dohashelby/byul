"""Read narrow serving metrics from the exact owned GPU QA Pod."""

import ipaddress
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


METRICS = frozenset((
    'num_requests_running', 'num_requests_waiting', 'num_requests_swapped',
    'kv_cache_usage_perc', 'gpu_cache_usage_perc', 'request_success_total',
    'prompt_tokens_total', 'generation_tokens_total', 'request_queue_time_seconds_count',
    'request_queue_time_seconds_sum', 'request_prefill_time_seconds_count',
    'request_prefill_time_seconds_sum', 'request_inference_time_seconds_count',
    'request_inference_time_seconds_sum', 'e2e_request_latency_seconds_count',
    'e2e_request_latency_seconds_sum',
))
LINE = re.compile(r'vllm:([a-z0-9_]+)(?:\{([^}\r\n]*)\})?\s+([0-9eE+.-]+)(?:\s+[0-9]+)?')
FINISH = re.compile(r'(?:^|,)\s*finished_reason="(stop|length|abort|cancelled|tool_calls|error|unknown)"(?:,|$)')


def read_series(raw):
    series = {}
    for line in raw.decode('utf-8').splitlines():
        match = LINE.fullmatch(line)
        if match is None:
            continue
        name, labels, value = match.groups()
        if name not in METRICS:
            continue
        try:
            parsed = float(value)
        except ValueError:
            continue
        if not math.isfinite(parsed):
            continue
        if name == 'request_success_total' and (reason := FINISH.search(labels or '')):
            name += '.' + reason.group(1)
        series.setdefault(name, []).append(parsed)
    return series


def collect(url, expected_ip, count):
    ip = ipaddress.ip_address(expected_ip)
    parts = urlsplit(url)
    if type(count) is not int or not 1 <= count <= 40 or (
            parts.scheme, parts.hostname, parts.port, parts.path, parts.query, parts.fragment, parts.username, parts.password
    ) != ('http', str(ip), 8000, '', '', '', None, None):
        raise ValueError('metrics target is outside the verified engine Pod')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    rows = []
    for i in range(count):
        stamp = {'utc_start_ns': time.time_ns(), 'monotonic_start_ns': time.monotonic_ns()}
        try:
            with opener.open(urllib.request.Request(url + '/metrics', headers={'Connection': 'close'}), timeout=2) as response:
                raw = response.read(1048577)
                if len(raw) > 1048576:
                    raise ValueError('metrics response exceeds the capture limit')
            observed = {'http_status': response.status, 'verified_numeric_series': read_series(raw)}
        except urllib.error.HTTPError as error:
            observed = {'http_status': error.code}
        except (OSError, UnicodeError, ValueError) as error:
            observed = {'error': type(error).__name__}
        rows.append({**stamp, **observed, 'utc_finish_ns': time.time_ns(), 'monotonic_finish_ns': time.monotonic_ns()})
        if i + 1 < count:
            time.sleep(0.8)
    return rows


if __name__ == '__main__':
    print(json.dumps(collect(sys.argv[1], sys.argv[2], int(sys.argv[3])), sort_keys=True, allow_nan=False))
