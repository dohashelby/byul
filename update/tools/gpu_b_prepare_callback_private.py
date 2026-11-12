import hashlib,json,pathlib,sys,guard
import os
from guard.collectors.engine_callbacks import prepare_assets,load_prepared
from guard.models import canonical_json
assert pathlib.Path(guard.__file__).resolve().is_relative_to(pathlib.Path(sys.argv[1])/'update/src/guard')
os.umask(0o077)
root=pathlib.Path(sys.argv[2]);manifest=prepare_assets(root,runner_consumption=True)
assert load_prepared(root)==manifest
settings_raw=(root/'settings.json').read_bytes();settings=json.loads(settings_raw)
assert manifest['session']==settings['session'] and len(bytes.fromhex(settings['secret']))==32
print(json.dumps({'manifest':manifest,'manifest_canonical_sha256':hashlib.sha256(canonical_json(manifest)).hexdigest(),
    'settings_file_sha256':hashlib.sha256(settings_raw).hexdigest(),'secret_exported':False,'engine_started':False}))
