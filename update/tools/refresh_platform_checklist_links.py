"""선정 37개 범위를 보존하며 platform의 update 팩 연결 메타데이터를 재생성한다."""
import argparse
import ast
import hashlib
from pathlib import Path
import pprint

import yaml

ROOT = Path(__file__).resolve().parents[2]


def refresh(repo=ROOT, *, write=False):
    repo = Path(repo)
    target = repo / 'platform/api/src/byul_platform/sources/checklist_data.py'
    pack = repo / 'update/packs/PACK-BASELINE'
    manifest = yaml.safe_load((pack / 'pack.yaml').read_bytes())
    source = target.read_text(encoding='utf-8')
    nodes = {node.targets[0].id: node for node in ast.parse(source).body
             if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
    rows = ast.literal_eval(nodes['CHECKS'].value)
    if len(rows) != 37 or len({row['framework_id'] for row in rows}) != 37:
        raise ValueError('선정 37개 범위가 달라 연결표 재생성을 거부합니다.')
    for row in rows:
        link = row['update']
        raw = (pack / 'checks' / (link['id'] + '.yaml')).read_bytes()
        check = yaml.safe_load(raw)
        if check['framework_id'] != row['framework_id']:
            raise ValueError('프레임워크 ID가 달라 연결표 재생성을 거부합니다.')
        link.update(revision=check['revision'], retired=check['retired'],
                    executor=check['executor'], sha256=hashlib.sha256(raw).hexdigest())
    lines = source.splitlines(keepends=True)
    for key, value in sorted((('CHECKS', pprint.pformat(rows, sort_dicts=False)),
                              ('PACK_REVISION', str(manifest['revision']))),
                             key=lambda item: nodes[item[0]].lineno, reverse=True):
        node = nodes[key]
        lines[node.lineno-1:node.end_lineno] = [f'{key} = {value}\n']
    result = ''.join(lines)
    result = result.replace('고정 PACK33 CheckDef/framework_id를 대조한 메타데이터.',
        'runtime 기준은 FIXED_SHA로 보존하고 현행 update 팩의 CheckDef/framework_id를 대조한 메타데이터.')
    changed = result != source
    if write and changed:
        target.write_text(result, encoding='utf-8')
    return changed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    changed = refresh(write=args.write)
    if changed and not args.write:
        raise SystemExit('현행 팩 연결 메타데이터 재생성이 필요합니다.')
    print('연결표 재생성 완료' if changed else '연결표 일치')
