"""실행: python -m guard.gpu18_prototype --output <새 디렉토리>."""

import argparse
import json
from pathlib import Path

from .runner import run_demo


def main():
    parser = argparse.ArgumentParser(description="18개 항목의 EXAMPLE 모의 연동·저장·복원 검증")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = run_demo(args.output)
    except (ValueError, FileExistsError) as exc:
        parser.exit(2, f"모의 실행 중단: {exc}\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
