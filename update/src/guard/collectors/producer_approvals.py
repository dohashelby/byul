"""Independent release receipts. This file/data are outside producer releases.

Installing a receipt is an explicit reviewed release change; observed hashes
never register themselves. Empty shipped receipts confer no qualification.
"""
import json
from pathlib import Path
from .producer_release import Qualification

PATH = Path(__file__).with_name('producer_qualifications.json')


def receipts(path=PATH):
    value = json.loads(Path(path).read_bytes())
    if set(value) != {'schema', 'receipts'} or value['schema'] != 'guard-producer-qualifications/1' or not isinstance(value['receipts'], list):
        raise ValueError('PRODUCER_RECEIPTS_INVALID')
    return tuple(value['receipts'])


def legacy_scoped(path=PATH):
    rows = [r for r in receipts(path) if r['wire_schema'] == 'guard-gpu-common-input/2']
    supported, qualified = {}, []
    for row in rows:
        scope = row['scope']
        receipt = Qualification(row['receipt'], row['wire_schema'], row['producer']['source_sha256'],
            **scope, stage_ranks=tuple((k, tuple(v)) for k, v in row['stage_ranks'].items()))
        key = row['producer']['name'], row['producer']['version']
        if key in supported and supported[key] != receipt.producer_sha256:
            raise ValueError('PRODUCER_RECEIPTS_AMBIGUOUS')
        supported[key] = receipt.producer_sha256; qualified.append(receipt)
    return supported, tuple(qualified)


def qualified(producer, schema, scope, *, path=PATH):
    return any(r['producer'] == producer and r['wire_schema'] == schema and r['scope'] == scope for r in receipts(path))
