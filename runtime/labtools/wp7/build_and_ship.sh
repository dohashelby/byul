#!/usr/bin/env bash
# Local build procedure. Default displays a plan; transport is gated separately.
set -euo pipefail
base=7741687d7f365bab3ab9681bc536e8c6250f2af6
mode=--dry-run
if (($#)); then mode=$1; fi
if [[ "$mode" == --dry-run ]]; then
  cat <<'PLAN'
1. Export baseline 7741687d runtime tree to a new temporary directory.
2. Require reviewed PYTHON_IMAGE=python:3.12-slim@sha256:<digest>.
3. docker build --platform linux/amd64 --network=none; tag serving-gateway:rt-7741687d.
4. docker save; record config digest and tar hash; git bundle baseline.
5. Stop before transport. execute_from_mac.py requires explicit AWS approval.
6. After approval: copy through private SSH alias; verify bundle and image hashes.
7. Server: git bundle verify, separate baseline checkout, containerd image import.
PLAN
  exit 0
fi
[[ "$mode" == --build ]] || exit 2
[[ -n "$REPO_ROOT" && -n "$ARTIFACT_DIR" && -n "$PYTHON_IMAGE" ]] || exit 2
[[ "$PYTHON_IMAGE" =~ ^python:3\.12-slim@sha256:[0-9a-f]{64}$ ]] || exit 2
[[ ! -e "$ARTIFACT_DIR" ]] || exit 2
umask 077
mkdir -p "$ARTIFACT_DIR/source"
git -C "$REPO_ROOT" cat-file -e "$base^{commit}"
(umask 022; git -C "$REPO_ROOT" archive "$base" runtime | tar -x -C "$ARTIFACT_DIR/source")
docker build --platform linux/amd64 --network=none --label "org.opencontainers.image.revision=$base" -f "$ARTIFACT_DIR/source/runtime/deploy/gateway/Dockerfile" \
  --build-arg "PYTHON_IMAGE=$PYTHON_IMAGE" -t serving-gateway:rt-7741687d "$ARTIFACT_DIR/source/runtime"
docker save -o "$ARTIFACT_DIR/serving-gateway-rt-7741687d.tar" serving-gateway:rt-7741687d
[[ "$(git -C "$REPO_ROOT" rev-parse HEAD)" == "$base" ]] || {
  echo 'Bundle requires baseline HEAD; use a separate baseline checkout'; exit 2;
}
git -C "$REPO_ROOT" bundle create "$ARTIFACT_DIR/byul-7741687d.bundle" HEAD
python3 - "$ARTIFACT_DIR" <<'PY'
import hashlib,json,pathlib,tarfile,sys,os
p=pathlib.Path(sys.argv[1]);rows={}
for name in ('serving-gateway-rt-7741687d.tar','byul-7741687d.bundle'):
 rows[name]=hashlib.sha256((p/name).read_bytes()).hexdigest()
with tarfile.open(p/'serving-gateway-rt-7741687d.tar') as t:
 manifest=json.load(t.extractfile('manifest.json'));assert len(manifest)==1;config=manifest[0]['Config']
 raw=t.extractfile(config).read();image=json.loads(raw)
 assert image['architecture']=='amd64' and image['os']=='linux'
 assert 'serving-gateway:rt-7741687d' in manifest[0]['RepoTags']
 rows['image_config_sha256']=hashlib.sha256(raw).hexdigest()
 rows['platform']='linux/amd64';rows['base_image']=os.environ['PYTHON_IMAGE']
 rows['baseline_commit']='7741687d7f365bab3ab9681bc536e8c6250f2af6'
(p/'build.json').write_text(json.dumps(rows,indent=2)+'\n')
PY
echo 'Local artifacts saved. Transport still requires AWS execution approval.'
