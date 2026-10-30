"""고정 native profile의 요청/모델 객체 관계를 진단 투영한다. 최종 소비를 인증하지 않는다."""
from datetime import datetime
import re

def _sha(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None

def _time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def project_configured_models(packet, attempts, *, locators=False):
    """Only the internal current capture_session result is consumed, never an import.

    The old index's explicit false final-model/consumption flags remain false.
    Correlated configured-object claims are reported separately from proof.
    """
    index = (packet or {}).get("request_index", {})
    recordings = (packet or {}).get("recordings", [])
    trials = []
    for attempt in attempts:
        row = {**attempt, "native_request_linked": False, "configured_model_candidates": [],
               "final_model_identity_verified": False, "principal_identity_verified": False,
               "diagnostic_codes": []}
        matches = []
        for domain in index.get("domains", {}).values():
            links = [a for a in domain.get("assignments", [])
                     if a.get("asgi_request_sha256") == attempt["request_id_sha256"]]
            native_ids = {a["internal_sha256"] for a in links}
            if len(native_ids) != 1:
                continue
            native_id, = native_ids
            for candidate in domain.get("model_observations", []):
                if (candidate.get("point") == "GPUModelRunner._model_forward"
                        and candidate.get("phase") == "RETURN"
                        and native_id in candidate.get("request_sha256", [])):
                    matches.append((candidate, native_id, links))
        if not matches:
            row["diagnostic_codes"].append("FINAL_MODEL_NATIVE_LINK_UNAVAILABLE")
        else:
            row["native_request_linked"] = True
            if len(matches) > 64:
                row["diagnostic_codes"].append("MODEL_NATIVE_CANDIDATE_BOUND")
            for candidate, native_id, assignments in matches[:64]:
                capture_index, seq = candidate["event"]
                capture = recordings[capture_index]["capture"]
                model_ref = candidate["metadata"].get("owner_model_sha256")
                if model_ref is None:
                    continue
                loaded = [];loader_sequences=[]
                for event in capture["records"]:
                    payload = event["payload"]
                    metadata = payload.get("metadata", {}).get("model_selection", {})
                    if (event["seq"] < seq and event["event"] == "NATIVE"
                            and payload.get("point") == "BaseModelLoader.load_model"
                            and payload.get("phase") == "RETURN"
                            and metadata.get("returned_model_sha256") == model_ref):
                        loaded.append(metadata);loader_sequences.append(event['seq'])
                pairs = {(r.get("configured_model_sha256"), r.get("configured_revision_sha256")) for r in loaded}
                safe = {"native_request_sha256": native_id, "session_model_object_sha256": model_ref,
                        "configured_model_sha256": None, "configured_revision_sha256": None,
                        "same_process_loader_object_linked": len(pairs) == 1,
                        "source_process_context_verified": capture.get("process_context_verified") is True,
                        "source_image_digest_verified": capture.get("image_digest_verified") is True,
                        "file_tensor_identity_verified": False}
                if len(pairs) == 1:
                    safe["configured_model_sha256"], safe["configured_revision_sha256"] = next(iter(pairs))
                if locators:
                    safe['native_locator']={'recording':capture_index,'forward_seq':seq,'loader_sequences':loader_sequences[:64],
                        'loader_sequences_complete':len(loader_sequences)<=64,
                        'same_process_request_assignment_observed':any(a.get('event',[None])[0]==capture_index for a in assignments),
                        'context':dict(capture['records'][0]['payload']['context']),
                        'capture_sha256':capture['provenance']['capture_sha256'],
                        'recording_closed':capture.get('summary',{}).get('recording_closed') is True,
                        'recorded_modules':[{k:event['payload'].get(k) for k in ('module','verified','source_sha256','code_sha256')}
                            for event in capture['records'] if event['event']=='MODULE']}
                row["configured_model_candidates"].append(safe)
            row["diagnostic_codes"].extend(["PRINCIPAL_BINDING_UNVERIFIED", "FINAL_MODEL_IDENTITY_UNVERIFIED"])
        trials.append(row)
    return trials


def project_native(packet, attempts, *, grants, retired, principals):
    """Existing REQ05 policy projection; neutral model facts are shared with OBS02."""
    trials=project_configured_models(packet,attempts);conflicts=[];retired_uses=[]
    for row in trials:
        for safe in row['configured_model_candidates']:
            model,revision=safe['configured_model_sha256'],safe['configured_revision_sha256']
            if (_sha(model) and _sha(revision) and row['attempted']
                    and row['binding_verified'] and row['policy_window_verified']):
                principal=principals[row['role']]
                if (principal,model,revision) not in grants:
                    conflicts.append({'case_id':row['case_id'],'model_sha256':model,
                        'revision_sha256':revision,'confirmed_violation':False})
                for rm,rr,deadline in retired:
                    if model==rm and revision==rr and _time(row['started_at'])>=deadline:
                        retired_uses.append({'case_id':row['case_id'],'model_sha256':model,
                            'revision_sha256':revision,'confirmed_violation':False,'time_basis':'collector_UTC_attempt_start'})
    return trials,conflicts,retired_uses

