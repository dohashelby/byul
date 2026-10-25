"""OpenAPI 문서를 파일로 쓴다. 화면 타입 생성(openapi-typescript)의 입력이다.

실행: uv run python -m byul_platform.export_openapi ../web/src/api/openapi.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .main import create_app


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")
    app = create_app()
    try:
        out.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        app.state.byul.close()
    print(out)


if __name__ == "__main__":
    main()
