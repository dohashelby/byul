"""과거 PACK-BASELINE의 불변 원본 위치. 파생 빌더·시험의 seed 입력은 이 원본만 읽는다.

SEED_REF는 사람이 검토해 고정한 값이다. 현행 팩이 바뀌어도 자동 갱신하지 않는다.
"""
from pathlib import Path

from tools.build_kubernetes_api_pack import SEED_REF

ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = ROOT / "reference/pack-seeds/PACK-BASELINE-31"

__all__ = ["SEED_DIR", "SEED_REF"]
