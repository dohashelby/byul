"""Server-local transport integrity check; performs no network operations."""
import json
from pathlib import Path
from common import digest

def main():
    root=Path(__file__).resolve().parent
    expected=json.loads((root/'build.json').read_text())
    for name in ['byul-7741687d.bundle','serving-gateway-rt-7741687d.tar']:
        if digest(root/name)!=expected[name]:raise ValueError('ARTIFACT_HASH_MISMATCH')
    print('baseline_bundle_and_image_transport_hashes=matched')
if __name__=='__main__':main()
