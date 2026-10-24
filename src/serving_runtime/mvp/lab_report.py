"""Read-only, secret-minimized Kubernetes lab report view."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .mock_engine import _utc_now


REPORT_SCHEMA = "serving-runtime-k8s-lab-report/1.0"
CASE_IDS = tuple(f"L-{index:02d}" for index in range(1, 7))


class LabReportSource:
    def __init__(self, path: str | Path | None) -> None:
        self.configured_path = None if path is None else Path(path).expanduser()

    def _resolve(self) -> Path | None:
        path = self.configured_path
        if path is None:
            return None
        if path.is_dir():
            candidates = sorted(
                (candidate for candidate in path.glob("*.json") if candidate.is_file()),
                key=lambda candidate: (candidate.stat().st_mtime_ns, candidate.name),
                reverse=True,
            )
            if not candidates:
                raise FileNotFoundError("lab report directory has no JSON reports")
            return candidates[0]
        return path

    def read(self) -> dict[str, Any]:
        generated_at = _utc_now()
        if self.configured_path is None:
            return {
                "state": "not_configured",
                "configured_path": None,
                "path": None,
                "error": None,
                "report": None,
                "generated_at": generated_at,
            }
        try:
            path = self._resolve()
            assert path is not None
            raw = path.read_bytes()
            document = json.loads(raw.decode("utf-8"))
            report = self._validate(document)
        except FileNotFoundError as error:
            state = "missing"
            message = str(error)
            path_text = None
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
            state = "error"
            message = str(error)
            path_text = str(path) if "path" in locals() and path is not None else None
        else:
            return {
                "state": "ok",
                "configured_path": str(self.configured_path),
                "path": str(path),
                "error": None,
                "report": report,
                "generated_at": generated_at,
            }
        return {
            "state": state,
            "configured_path": str(self.configured_path),
            "path": path_text,
            "error": message,
            "report": None,
            "generated_at": generated_at,
        }

    @staticmethod
    def _validate(document: Any) -> dict[str, Any]:
        if not isinstance(document, dict) or document.get("schema") != REPORT_SCHEMA:
            raise ValueError("unsupported lab report schema")
        for name in ("run_id", "started_at", "finished_at", "context", "namespace", "overall"):
            if not isinstance(document.get(name), str) or not document[name]:
                raise ValueError(f"lab report {name} must be a non-empty string")
        raw_cases = document.get("cases")
        if not isinstance(raw_cases, list):
            raise ValueError("lab report cases must be an array")
        cases: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_case in raw_cases:
            if not isinstance(raw_case, dict):
                raise ValueError("lab report case must be an object")
            case_id = raw_case.get("id")
            if case_id not in CASE_IDS or case_id in seen:
                raise ValueError("lab report case IDs must be unique L-01 through L-06")
            seen.add(case_id)
            status = raw_case.get("status")
            title = raw_case.get("title")
            summary = raw_case.get("summary")
            evidence = raw_case.get("evidence")
            if (
                not isinstance(status, str)
                or not isinstance(title, str)
                or not isinstance(summary, str)
                or not isinstance(evidence, list)
                or any(not isinstance(item, str) for item in evidence)
            ):
                raise ValueError(f"lab report case {case_id} has invalid fields")
            cases.append(
                {
                    "id": case_id,
                    "title": title,
                    "status": status,
                    "summary": summary,
                    "evidence": evidence,
                    "limitation": raw_case.get("limitation")
                    if isinstance(raw_case.get("limitation"), str)
                    else None,
                    "note": raw_case.get("note")
                    if isinstance(raw_case.get("note"), str)
                    else None,
                }
            )
        if seen != set(CASE_IDS):
            raise ValueError("lab report must contain L-01 through L-06")
        cases.sort(key=lambda item: item["id"])
        return {
            "schema": REPORT_SCHEMA,
            "run_id": document["run_id"],
            "started_at": document["started_at"],
            "finished_at": document["finished_at"],
            "context": document["context"],
            "namespace": document["namespace"],
            "cleanup_requested": document.get("cleanup_requested") is True,
            "overall": document["overall"],
            "cases": cases,
        }

