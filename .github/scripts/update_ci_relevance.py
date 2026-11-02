"""PR에서 update CI를 돌릴지 정한다. push는 workflow 경로 필터가 이미 걸렀다."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys

PATHS = Path(__file__).resolve().parents[1] / 'update-ci-paths.txt'


def patterns(path=PATHS):
    return [line.strip() for line in path.read_text(encoding='utf-8').splitlines()
        if line.strip() and not line.lstrip().startswith('#')]


def _regex(pattern):
    out = ''; i = 0
    while i < len(pattern):
        if pattern.startswith('**/', i):
            out += '(?:.*/)?'; i += 3
        elif pattern.startswith('**', i):
            out += '.*'; i += 2
        elif pattern[i] == '*':
            out += '[^/]*'; i += 1
        else:
            out += re.escape(pattern[i]); i += 1
    return re.compile(out + r'\Z')


def relevant(files, rules):
    """GitHub 경로 필터처럼 마지막으로 맞은 규칙을 따른다."""
    compiled = [(rule.startswith('!'), _regex(rule.lstrip('!'))) for rule in rules]
    for name in files:
        matched = False
        for negated, regex in compiled:
            if regex.match(name):
                matched = not negated
        if matched:
            return True
    return False


def main():
    if os.environ.get('EVENT_NAME') != 'pull_request':
        print('relevant=true'); return
    base, head = os.environ['BASE_SHA'], os.environ['HEAD_SHA']
    files = subprocess.check_output(['git', 'diff', '--name-only', f'{base}...{head}'], text=True).splitlines()
    result = relevant(files, patterns())
    print(f'relevant={str(result).lower()}')
    print(f'changed files: {len(files)}, relevant: {result}', file=sys.stderr)


if __name__ == '__main__':
    main()
