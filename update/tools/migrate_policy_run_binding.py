"""Write a PACK-BASELINE@35 policy input from an @34 one (RB, 2026-11-14).

Only the run-binding keys (ENV/target/prepared/manifest hashes and the
observation window that guard run now computes, `guard-run-binding/1`) are
removed and the pack reference is moved to the given pack.  Every enterprise
value is kept as is.  The original file is never changed.  Because the
document changes, the output is a DRAFT and any confirmation must be given
again by a person (INV-07).  A sealed POL object (kind: POL) is not rewritten:
it stays valid for the pack it references.
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
    removed = {cid: sorted(set(row or {}) & set(run_binding.keys_for(pack.checks[cid])))
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
    args = parser.parse_args(argv)
    if args.out.exists():
        raise SystemExit('output exists; choose a new path')
    out, removed = migrate(yaml.safe_load(args.policy.read_text(encoding='utf-8')), load_pack(args.pack))
    args.out.write_text(yaml.safe_dump(out, allow_unicode=True, sort_keys=False), encoding='utf-8')
    print(yaml.safe_dump({'removed_run_binding_keys': removed, 'status': out.get('status')}, allow_unicode=True, sort_keys=False))


if __name__ == '__main__':
    main()
