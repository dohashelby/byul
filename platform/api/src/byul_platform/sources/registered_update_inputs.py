"""Resolve immutable platform revisions and convert them at the existing guard boundary."""

from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
from pathlib import Path

from ..config import Settings
from ..contracts import Contracts, canonical_json, canonical_sha256
from ..store import DocumentStore, InvalidDocId
from ..update_execution_models import LegacyUpdateRunRequest, UpdateRunRequest


class RegisteredInputError(ValueError):
    pass


class RegisteredUpdateInputs:
    def __init__(self, settings: Settings, contracts: Contracts, environments: DocumentStore, policies: DocumentStore):
        self.settings, self.contracts = settings, contracts
        self.environments, self.policies = environments, policies
        self.update_dir = settings.update_dir.expanduser().resolve()
        config = settings.update_config_file or Path("examples/gpu-bounded.toml")
        self.config = (self.update_dir / config.expanduser()).resolve()

    def unavailable_reason(self) -> str | None:
        if not self.config.is_file():
            return "서버의 검증 실행 설정이 준비되지 않았습니다. 관리자에게 문의하세요"
        try:
            import guard.models  # noqa: F401 - the existing optional update integration
        except ImportError:
            return "서버의 update 입력 연동 모듈이 준비되지 않았습니다. 관리자에게 문의하세요"
        if not (self.update_dir / "tools/import_byul_inputs.py").is_file():
            return "서버의 검증 입력 변환 도구를 찾을 수 없습니다. 관리자에게 문의하세요"
        return None

    def documents(self, request: UpdateRunRequest) -> dict:
        documents = {}
        selections = {"policy": request.policy, "environment": request.environment,
                      "baseline": request.baseline, "candidate": request.candidate}
        for role, selection in selections.items():
            if selection is None:
                continue
            store = self.policies if role == "policy" else self.environments
            try:
                stored = store.get(selection.id, selection.revision)
            except (InvalidDocId, OSError, ValueError):
                stored = None
            if stored is None:
                label = {"policy": "정책", "environment": "환경", "baseline": "기준 환경", "candidate": "후보 환경"}[role]
                raise RegisteredInputError(f"선택한 {label} 판본이 없습니다. 등록된 판본을 다시 선택하세요")
            kind = "policy" if role == "policy" else "environment"
            if (stored.doc_id != selection.id or stored.revision != selection.revision
                    or stored.sha256 != canonical_sha256(stored.document)
                    or self.contracts.errors(kind, stored.document)):
                raise RegisteredInputError("저장된 환경·정책 판본의 형식 또는 해시를 확인할 수 없습니다")
            if role != "policy" and (stored.document["environment_id"] != stored.doc_id or stored.document["revision"] != stored.revision):
                raise RegisteredInputError("저장된 환경과 선택한 판본 정보가 다릅니다")
            if role == "policy" and stored.document["policy_id"] != stored.doc_id:
                raise RegisteredInputError("저장된 정책과 선택한 정책 ID가 다릅니다")
            documents[role] = stored
        environment = documents["environment" if request.mode == "validate" else "baseline"]
        policy = documents["policy"].document
        expected = {"id": environment.doc_id, "revision": environment.revision, "sha256": environment.sha256}
        if policy["environment_ref"] != expected:
            raise RegisteredInputError("정책이 참조하는 환경 판본과 선택한 환경이 다릅니다. 정책의 기준 환경 판본을 선택하세요")
        for role in ("environment", "baseline", "candidate"):
            if role in documents and documents[role].document["target"]["target_ref"] != policy["target_ref"]:
                raise RegisteredInputError("선택한 환경의 대상과 정책의 적용 대상이 다릅니다")
        return documents

    @staticmethod
    def refs(documents: dict) -> dict:
        return {role: {"id": stored.doc_id, "revision": stored.revision, "sha256": stored.sha256}
                for role, stored in documents.items()}

    def _boundary(self):
        spec = importlib.util.spec_from_file_location("byul_registered_input_boundary", self.update_dir / "tools/import_byul_inputs.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def write(self, request: UpdateRunRequest, folder: Path) -> tuple[LegacyUpdateRunRequest, dict, dict]:
        from guard.models import Environment, load_pack, new_id

        documents = self.documents(request)
        policy = documents["policy"].document
        wanted = policy["verification"]["pack"]
        # The stored policy chooses the exact Pack revision. Never upgrade a policy in place.
        pack_path = self.update_dir / "packs" / wanted["id"]
        if not pack_path.resolve().is_relative_to((self.update_dir / "packs").resolve()):
            raise RegisteredInputError("정책의 검증 기준을 확인할 수 없습니다")
        try:
            pack = load_pack(pack_path)
        except (OSError, ValueError, KeyError):
            raise RegisteredInputError("정책이 사용하는 검증 기준이 서버에 준비되지 않았습니다") from None
        if {"id": pack.ref().id, "revision": pack.ref().revision} != wanted:
            raise RegisteredInputError("정책의 검증 기준 판본이 서버와 다릅니다. 같은 Pack 판본을 준비하거나 새 정책을 등록하세요")
        boundary = self._boundary()
        side = "environment" if request.mode == "validate" else "baseline"
        try:
            environment, converted_policy, mapping = boundary.convert_documents(
                documents[side].document, policy, pack, self.contracts, as_of=datetime.now(UTC), version=2)
        except boundary.InputBoundaryError as error:
            raise RegisteredInputError(f"선택한 환경·정책을 검증 입력으로 변환할 수 없습니다 ({error})") from None
        payloads = {f"{side}.json": environment.to_dict(), "policy.json": converted_policy.to_dict()}
        provenance = {"schema": "byul-platform-update-inputs/1", "source_refs": self.refs(documents),
                      "policy_binding": side, "import": mapping}
        if request.mode == "update":
            # A comparison uses the baseline's common policy. Convert only the candidate
            # ENV: do not rebind, copy or manufacture a candidate policy/approval.
            source = documents["candidate"].document
            target = source["target"]
            runtime = target.get("runtime")
            if runtime is None or any(key not in runtime for key in ("nodes", "tp_groups")):
                raise RegisteredInputError("후보 환경의 노드·병렬 구성 입력이 필요합니다")
            try:
                candidate = Environment(id=new_id("ENV"), created_at=source["created_at"], data_origin=source["data_origin"],
                    engine={key: target["engine"][key] for key in (*boundary._GUARD_ENGINE_KEYS, "runtime_kind", "frontend") if key in target["engine"]},
                    runtime={key: runtime[key] for key in (*boundary._GUARD_RUNTIME_KEYS, "tp_size", "pp_size", "dp_size") if key in runtime},
                    model={key: target["model"].get(key) for key in ("id", "revision", "inventory_sha256", "tokenizer_revision", "components")},
                    facts=source["facts"]).seal()
            except ValueError:
                raise RegisteredInputError("후보 환경을 검증 입력으로 변환할 수 없습니다. 모델·병렬 구성 입력을 확인하세요") from None
            payloads["candidate.json"] = candidate.to_dict()
            provenance["candidate_environment_ref"] = candidate.ref().model_dump(mode="json")
            provenance["candidate_limitations"] = mapping["limitations"]
        payloads.update({f"source-{role}.json": stored.document for role, stored in documents.items()})
        payloads["provenance.json"] = provenance
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name, document in payloads.items():
            path = folder / name
            path.write_bytes(canonical_json(document))
            path.chmod(0o600)
        files = LegacyUpdateRunRequest(mode=request.mode, pack_path=str(pack_path), config_path=str(self.config),
            policy_path=str(folder / "policy.json"),
            **({"environment_path": str(folder / "environment.json")} if request.mode == "validate" else
               {"baseline_path": str(folder / "baseline.json"), "candidate_path": str(folder / "candidate.json")}))
        return files, self.refs(documents), pack.ref().model_dump(mode="json")

    def check(self, request: UpdateRunRequest, expected: dict) -> None:
        documents = self.documents(request)
        if self.refs(documents) != expected:
            raise RegisteredInputError("준비 점검 후 선택한 환경·정책 판본이 바뀌었습니다. 다시 준비 점검하세요")
        validity = documents["policy"].document["validity"]
        now = datetime.now(UTC)
        for key, future in (("valid_from", True), ("valid_until", False)):
            if validity[key] is not None:
                value = datetime.fromisoformat(validity[key].replace("Z", "+00:00"))
                if (future and now < value) or (not future and now >= value):
                    raise RegisteredInputError("정책의 유효 기간이 현재 실행 시각과 맞지 않습니다. 정책을 확인하세요")
