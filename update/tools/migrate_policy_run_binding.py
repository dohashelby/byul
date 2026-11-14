"""Write a PACK-BASELINE@35/@36 policy input from an older one (RB, RB2, 2026-11-14).

Only the run-binding keys (ENV/target/prepared/manifest hashes and the
observation window that guard run now computes, `guard-run-binding/1`) are
removed and the pack reference is moved to the given pack.  Every enterprise
value is kept as is.  The original file is never changed.  Because the
document changes, the output is a DRAFT and any confirmation must be given
again by a person (INV-07).  A sealed POL object (kind: POL) is not rewritten:
it stays valid for the pack it references.

Stage 2 (@36, RB2-Q7): the removed run-preparation values (record sessions,
transition generations/times, PLT-07/08 identities) are not policy values any
more but per-check `[run_binding.<CHECK>]` config entries.  `--stage 2
--run-config-out FILE` writes them as an operator DRAFT to review; nothing is
applied automatically and the REQ-02 declared record-file hash is dropped
(RB2-Q5: the read file's hash is recorded instead).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from guard.executors import run_binding
from guard.models import load_pack

ROOT = Path(__file__).resolve().parents[1]


def migrate(document: dict, pack) -> tuple[dict, dict[str, list[str]]]:
    if document.get('kind') == 'POL':
        raise ValueError('RUN_BINDING_SEALED_POLICY_UNCHANGED')
    removed = {cid: sorted(set(row or {}) & run_binding.owned_keys(pack.checks[cid]))
               for cid, row in (document.get('values') or {}).items() if cid in pack.checks}
    values = {cid: {k: v for k, v in (row or {}).items() if k not in removed.get(cid, ())}
              for cid, row in (document.get('values') or {}).items()}
    out = dict(document)
    out['pack'] = {'id': pack.manifest.pack_id, 'revision': pack.manifest.revision}
    out['values'] = values
    if any(removed.values()) or (document.get('pack') or {}).get('revision') != pack.manifest.revision:
        out['status'] = 'DRAFT'
        out['confirmed_by'] = out['confirmed_at'] = None
    return out, {cid: keys for cid, keys in removed.items() if keys}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('policy', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--pack', type=Path, default=ROOT / 'packs/PACK-BASELINE')
    parser.add_argument('--stage', type=int, choices=(1, 2), default=1)
    parser.add_argument('--run-config-out', type=Path)
    args = parser.parse_args(argv)
    if (args.stage == 2) != (args.run_config_out is not None):
        raise SystemExit('--stage 2 needs --run-config-out (and only stage 2 writes it)')
    for path in (args.out, args.run_config_out):
        if path is not None and path.exists():
            raise SystemExit('output exists; choose a new path')
    document = yaml.safe_load(args.policy.read_text(encoding='utf-8'))
    out, removed = migrate(document, load_pack(args.pack))
    args.out.write_text(yaml.safe_dump(out, allow_unicode=True, sort_keys=False), encoding='utf-8')
    if args.run_config_out is not None:
        candidates = run_binding.run_config_candidates(
            {cid: {k: v for k, v in (row or {}).items() if k in removed.get(cid, ())}
             for cid, row in (document.get('values') or {}).items()})
        args.run_config_out.write_text(yaml.safe_dump(
            {'status': 'DRAFT', 'run_binding': candidates}, allow_unicode=True, sort_keys=False), encoding='utf-8')
    print(yaml.safe_dump({'removed_run_binding_keys': removed, 'status': out.get('status')}, allow_unicode=True, sort_keys=False))


if __name__ == '__main__':
    main()
