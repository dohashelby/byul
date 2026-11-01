"""PACK31 원본에서 현행 PACK-BASELINE을 다시 만든다. 통합 때 pack.yaml revision이 한 번만 오른다."""
from pathlib import Path
import shutil

from tools.import_checklist import DEFAULT_OVERRIDES, DEFAULT_PACK, DEFAULT_SOURCE, import_checklist
from tools.pack_seed import SEED_DIR


def regenerate(pack: Path = DEFAULT_PACK) -> dict:
    shutil.rmtree(pack)
    shutil.copytree(SEED_DIR, pack)
    return import_checklist(DEFAULT_SOURCE, pack, DEFAULT_OVERRIDES)


if __name__ == "__main__":
    manifest = regenerate()
    print(f"{manifest['pack_id']} revision {manifest['revision']}: {len(manifest['checks'])} checks")
