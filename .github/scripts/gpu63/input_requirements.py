"""Static product input declarations, without targets, credentials or engine I/O."""
import argparse
import json
from pathlib import Path

from guard.executors import registry, run_binding
from guard.models import load_pack, utcnow
from datetime import timedelta

import sequential63
import stock_capture


def inventory():
    pack = load_pack(stock_capture.UPDATE / "packs/PACK-BASELINE")
    pol = sequential63.policy(pack, utcnow() + timedelta(minutes=45))
    cfg = sequential63.configuration(pack, Path("/declared/current/target.json"), pol)
    rows = {}
    for cid in sequential63.scope(pack):
        check = pack.checks[cid]
        slots = {}
        for key in run_binding.keys_for(check):
            try:
                field, loader = run_binding._source(cid, key)
            except run_binding.RunBindingUnavailable:
                continue
            slots[key] = {"executor_field": ".".join(field), "loader": loader}
        versions = {}
        for version in ("0.30.0", "0.31.0"):
            factory = registry.resolve_executor(check.executor, "kubernetes", engine="vllm", version=version)
            entry = {"registered": factory is not None, "policy_validation": "UNAVAILABLE"}
            if factory is not None:
                try:
                    factory(cfg).validate_policy(check, pol.values.get(cid, {}))
                except Exception as error:
                    # The only accepted exception text is a closed uppercase code.
                    # No target contents, environment values or arbitrary messages.
                    import re
                    code = str(error)
                    entry.update(policy_validation="REJECTED",
                                 code=code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,117}", code) else None,
                                 exception_type=type(error).__name__)
                else:
                    entry["policy_validation"] = "ACCEPTED_STATIC"
            versions[version] = entry
        rows[cid] = {"executor": check.executor, "collection_profile": check.collection_profile,
                     "run_binding_entries": sorted(run_binding.config_keys(check)),
                     "file_slots": slots,
                     "required_policy_fields": sorted(p.key for p in check.policy_params if p.required),
                     "versions": versions}
    assert len(rows) == 63
    return {"schema": "byul-single-gpu-input-requirements/1", "source_main": stock_capture.SOURCE_TREE,
            "engine_executed": False, "basis": "frozen product declarations; no runtime qualification",
            "requirements": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.write_text(json.dumps(inventory(), ensure_ascii=False, indent=2) + "\n")
