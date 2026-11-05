"""Fetch engine /metrics inside its Pod and save metric names only."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]*\Z")
REMOTE = """\
import json,re,urllib.request
name=re.compile(r'[A-Za-z_:][A-Za-z0-9_:]*')
with urllib.request.urlopen('http://127.0.0.1:8000/metrics',timeout=10) as response:
 raw=response.read(10485761)
if len(raw)>10485760: raise ValueError('metrics response too large')
names=set()
for line in raw.decode('utf-8').splitlines():
 if line.startswith('# TYPE ') or line.startswith('# HELP '):
  parts=line.split(' ',3)
  if len(parts)>2 and name.fullmatch(parts[2]): names.add(parts[2])
 elif line and not line.startswith('#'):
  matched=name.match(line)
  if matched and line[matched.end():matched.end()+1] in ('{',' ','\\t'): names.add(matched.group())
print(json.dumps(sorted(names)))
"""


def compare_names(names: list[str], config: dict) -> dict:
    if not names or any(not isinstance(name, str) or NAME.fullmatch(name) is None for name in names):
        raise ValueError("지표 이름 목록 형식 오류")
    mapping = config.get("metrics_map")
    if not isinstance(mapping, dict) or not mapping or any(
        not isinstance(item, dict) or item.get("verified") is not False or
        not isinstance(item.get("metric"), str) or NAME.fullmatch(item["metric"]) is None
        for item in mapping.values()
    ):
        raise ValueError("verified: false 후보 대응표가 아님")
    available = set(names)
    return {field: {"candidate": item["metric"], "found": item["metric"] in available}
            for field, item in sorted(mapping.items())}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("vllm", "sglang"), required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", default="serving-lab")
    parser.add_argument("--output", required=True, help="server-local name-only JSON output")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.context) or not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", args.namespace):
        parser.error("context or namespace format invalid")
    config_path = ROOT / "examples" / "mvp" / f"backend-{args.engine}.example.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    command = ["kubectl", "--context", args.context, "-n", args.namespace, "exec", "deploy/serving-engine", "-c", "engine", "--", "python3", "-c", REMOTE]
    try:
        result = subprocess.run(command, capture_output=True, timeout=20, check=False)
        if result.returncode:
            raise ValueError("엔진 Pod 내부 /metrics 이름 조회 실패")
        names = json.loads(result.stdout)
        if not isinstance(names, list):
            raise ValueError("지표 이름 목록 형식 오류")
        comparison = compare_names(names, config)
        output = {"schema": "runtime-aws-metric-names/1.0", "engine": args.engine,
                  "names": sorted(set(names)), "candidate_comparison": comparison,
                  "note": "값·라벨 값·원문은 저장하지 않음; verified는 false 유지"}
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(output, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except (OSError, ValueError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        print(f"이름 대조 실패: {type(error).__name__}")
        return 1
    print(f"{args.engine}: 이름 {len(output['names'])}개, 후보 {len(comparison)}개; {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
