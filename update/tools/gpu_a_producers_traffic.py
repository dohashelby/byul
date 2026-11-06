"""2단계 생산자 GPU 확인용 lab 요청(개발 담당 A). 시험 Pod 안에서 표준 라이브러리만으로 실행한다.

`v1-producers` 세대 엔진(개발 경로·torch profiler 켬)에 아래 순서로 요청을 보낸다. 판정하지 않고, 응답 상태와
크기·시간만 남긴다. 프롬프트와 응답 본문은 남기지 않는다.

1. health 두 번, 일반 요청 두 개, 스트림 도중 끊기 한 개 (request-attempt: finished·abort)
2. 동시 요청 여섯 개(`max_num_seqs` 2보다 많게, 대기열·선점 경로 후보). 센서 기록 한도(프로세스당 16 MiB) 안에 들도록 짧게 한다
3. 생성 옵션 요청: 온도·top_p·top_k·logprobs·허용 토큰·금지어·logit bias (runner-consumption)
4. 긴 요청 진행 중 `/collective_rpc reload_weights`, 뒤이어 `/reset_prefix_cache`와 새 요청 (generation-transition)
5. `/start_profile` → 요청 → `/stop_profile` (profiler-control)
6. 마지막 health

    kubectl exec -i <probe> -- python3 -I - --url http://serving-engine.serving-lab.svc:8000 < gpu_a_producers_traffic.py
"""
import argparse
import http.client
import json
import secrets
import threading
import time
import urllib.parse

MODEL = 'test-model'


def _conn(url, timeout):
    parts = urllib.parse.urlsplit(url)
    return http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)


def call(url, method, path, body=None, *, tag=None, timeout=300, stream_cut=None):
    started = time.monotonic()
    conn = _conn(url, timeout)
    headers = {'Content-Type': 'application/json'}
    if tag:
        headers['X-Request-Id'] = tag
    try:
        conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
        response = conn.getresponse()
        if stream_cut is not None:
            data = response.read(stream_cut)
            conn.sock.close()  # 스트림 도중 연결을 끊는다
            return {'path': path, 'tag': tag, 'status': response.status, 'bytes': len(data), 'cut': True,
                    'seconds': round(time.monotonic() - started, 3)}
        data = response.read()
        return {'path': path, 'tag': tag, 'status': response.status, 'bytes': len(data),
                'seconds': round(time.monotonic() - started, 3)}
    except Exception as error:  # 결과로만 남긴다
        return {'path': path, 'tag': tag, 'error': type(error).__name__, 'seconds': round(time.monotonic() - started, 3)}
    finally:
        conn.close()


def chat(text, max_tokens, **extra):
    return {'model': MODEL, 'messages': [{'role': 'user', 'content': text}], 'max_tokens': max_tokens, **extra}


TAGS = {}


def tag(case):
    # 센서는 요청 ID 안의 `byul-cb-<32 hex>`만 지문으로 쓴다. vLLM이 내부 ID에 붙이는 무작위 접미사와 무관하게
    # API 프로세스와 엔진·runner 쪽 행이 같은 요청으로 이어진다(a-p1-20261105-2에서 다른 형식 태그로 확인).
    value = f'byul-cb-{secrets.token_hex(16)}'
    TAGS[case] = value
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--kv-pressure', action='store_true', help='KV 압박 분기(v1-producers-kv): 긴 동시 요청 두 개만 보낸다')
    args = parser.parse_args()
    url = args.url.rstrip('/')
    out = {'schema': 'byul-gpu-a-producers-traffic/1', 'mode': 'kv-pressure' if args.kv_pressure else 'full', 'steps': []}
    step = lambda name, value: out['steps'].append({'step': name, **value})
    if args.kv_pressure:
        results = [None] * 2
        def long(i):
            results[i] = call(url, 'POST', '/v1/chat/completions',
                              chat(f'Tell story number {i} about mountains.', 400, ignore_eos=True), tag=tag(f'kv{i}'), timeout=600)
        threads = [threading.Thread(target=long, args=(i,)) for i in range(2)]
        [t.start() for t in threads]; [t.join() for t in threads]
        for i, value in enumerate(results):
            step(f'kv-{i}', value)
        out['tags'] = TAGS
        print(json.dumps(out, indent=1))
        return

    step('health-1', call(url, 'GET', '/health'))
    step('health-2', call(url, 'GET', '/health'))
    for i in (1, 2):
        step(f'normal-{i}', call(url, 'POST', '/v1/chat/completions', chat('Count from one to ten.', 32), tag=tag(f'normal{i}')))
    step('stream-cut', call(url, 'POST', '/v1/chat/completions', chat('Write a long story about a lighthouse.', 400, stream=True),
                            tag=tag('cut'), stream_cut=256))

    results = [None] * 6
    def worker(i):
        results[i] = call(url, 'POST', '/v1/chat/completions',
                          chat(f'Write {i + 3} paragraphs about rivers.', 64, ignore_eos=True), tag=tag(f'conc{i}'))
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    [t.start() for t in threads]; [t.join() for t in threads]
    for i, value in enumerate(results):
        step(f'concurrent-{i}', value)

    options = {'temperature': 0.7, 'top_p': 0.9, 'top_k': 20, 'logprobs': True, 'top_logprobs': 2,
               'allowed_token_ids': [11, 13, 15, 17, 19], 'bad_words': ['zebra'], 'logit_bias': {'11': 1.0}}
    step('options', call(url, 'POST', '/v1/chat/completions', chat('Say anything.', 16, **options), tag=tag('opt')))
    step('options-control', call(url, 'POST', '/v1/chat/completions', chat('Say anything.', 16), tag=tag('optc')))

    holder = {}
    long_tag = tag('inflight')
    thread = threading.Thread(target=lambda: holder.update(call(url, 'POST', '/v1/chat/completions',
                                                                  chat('Describe the ocean in detail.', 240, ignore_eos=True), tag=long_tag)))
    thread.start(); time.sleep(0.5)
    step('reload-weights', call(url, 'POST', '/collective_rpc', {'method': 'reload_weights'}))
    thread.join()
    step('inflight', holder)
    step('reset-prefix-cache', call(url, 'POST', '/reset_prefix_cache'))
    step('after-reset', call(url, 'POST', '/v1/chat/completions', chat('Count from one to ten.', 32), tag=tag('after')))

    step('start-profile', call(url, 'POST', '/start_profile'))
    step('profiled', call(url, 'POST', '/v1/chat/completions', chat('Name three colors.', 16), tag=tag('prof')))
    step('stop-profile', call(url, 'POST', '/stop_profile', timeout=600))
    step('health-3', call(url, 'GET', '/health'))
    out['tags'] = TAGS
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
