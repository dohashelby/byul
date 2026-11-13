"""B HTTP 소비자의 검토된 소스 조합. wire가 보고한 해시는 승인 입력이 아니다."""
from pathlib import Path
import json

_LEGACY_COMMON = frozenset({
    "f277fecd6df1e59505c0ad731032593f6fb22885",
    "df6255e96a877ebfbe185230ed87320aa84d2c30",
    "adcde856375a4de43d4a5d789c00de875e43e321",
    "2247aabb3447ccb3210b100cade06bce51ed771e",
})
_LEGACY = {
    "collector": "ab933ca4a869272d37143643f2bec0519c17e52af1a33814ae86f92338d312a6",
    "binding": "a63c81627e579129c64f2831f56ddc7cb1dfbb9b27cd98110541c10e21050db7",
    "probe": "96df266f30e713e43f13cc1a042896894cd061c5af7b7ffd286e8a13ae91e23d",
}
_CURRENT_RAW = {
    "update/src/guard/runtime/kubernetes_worker_readiness.py": "027cef9042248850410a59df160b603502f4ba28996a0138f8e597cc8e6d4a74",
    "update/src/guard/runtime/kubernetes_startup_binding.py": "39cd258e0108e73a5eeb4dcf91394a1a64f3b55724c0b1b6120b83d156a795e0",
    "update/src/guard/runtime/kubernetes_http.py": "2372565016384967160b101e4ae9493ea7d61e5c718f6ba9301154e7e015ea13",
    "update/src/guard/executors/stream_probe.py": "b37e17fe543cb352887f8ac214091b6f4899e63a972c63e3eb6ded819ae6656f",
}
_CURRENT_WRAPPER = "a84a47605aa38cb36303acc2b6c5f90e19e217be21cf476445f1b535f455a62d"


def expected_http_sources(proof):
    """검증된 full-source proof를 소비한다. 대상 IO 전에 호출한다.

    과거 profile은 과거 literal을 유지한다. manifest profile은 전체 검증과
    등록된 raw 소스 조합을 모두 통과해야 새 wrapper literal을 사용한다.
    """
    if type(proof) is not dict or type(proof.get("reviewed_common_commit")) is not str:
        raise ValueError("HTTP_REVIEWED_CONTRACT_UNAVAILABLE")
    common = proof["reviewed_common_commit"]
    if common in _LEGACY_COMMON:
        return dict(_LEGACY)
    from tools import verify_req10_consumption as gate
    repo = Path(__file__).resolve().parents[4]
    if common != gate.REVIEWED_COMMON_COMMIT or proof != gate.verify(repo):
        raise ValueError("HTTP_REVIEWED_CONTRACT_UNAVAILABLE")
    files = json.loads((repo / gate.MANIFEST_PATH).read_text(encoding="utf-8"))["files"]
    if any(files.get(name) != value for name, value in _CURRENT_RAW.items()):
        raise ValueError("HTTP_REVIEWED_CONTRACT_UNAVAILABLE")
    return {"collector": _CURRENT_RAW["update/src/guard/runtime/kubernetes_worker_readiness.py"],
            "binding": _CURRENT_RAW["update/src/guard/runtime/kubernetes_startup_binding.py"], "probe": _CURRENT_WRAPPER}
