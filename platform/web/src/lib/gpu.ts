import type { GpuSnapshot } from "../api/types";

export const GPU_REASON: Record<string, string> = {
  NVIDIA_SMI_MISSING: "nvidia-smi 없음",
  NVIDIA_SMI_FAILED: "nvidia-smi 실행 실패",
  TIMEOUT: "조회 시간 초과",
  EXEC_FAILED: "실행 권한·환경 오류",
  PARSE_ERROR: "출력 형식 오류",
  NO_GPU: "GPU 없음",
  TOO_MANY_GPUS: "GPU 수 제한 초과",
  OUTPUT_TOO_LARGE: "출력 크기 초과",
  INVALID_SNAPSHOT: "상태 파일 검증 실패",
};

/** 수집 중단(stale)·미연결은 0이 아니라 관측 불가로 표시한다. */
export function gpuChipState(item: GpuSnapshot | undefined): [string, "off" | "synthetic" | "real"] {
  if (!item || item.state === "not_configured") return ["미연결", "off"];
  if (item.state === "missing") return ["관측 불가 · 수집 결과 없음", "off"];
  if (item.state === "stale") return ["관측 불가 · 수집 중단(마지막 값 오래됨)", "off"];
  if (item.state === "error") return [`관측 불가 · ${GPU_REASON[item.reason ?? ""] ?? "오류"}`, "off"];
  return [`수집 정상 · ${item.gpus?.length ?? 0}개`, "real"];
}

export function fmt(value: number | null | undefined, unit: string, digits = 0): string {
  return value === null || value === undefined ? "–" : `${value.toFixed(digits)}${unit}`;
}
