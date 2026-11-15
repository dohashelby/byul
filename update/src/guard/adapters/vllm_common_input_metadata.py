"""공통 입력으로 내보낼 엔진 고유 KV 단위와 감싸기 기반 의존성."""
from pathlib import Path
from . import vllm_cache_callback

KV_UNIT = 'block'
# 토큰 수를 관측하지 않았으므로 임의의 block_size를 채우지 않는다.
KV_UNIT_TOKENS = None
SOURCE_PATHS = (Path(__file__), Path(vllm_cache_callback.__file__))
