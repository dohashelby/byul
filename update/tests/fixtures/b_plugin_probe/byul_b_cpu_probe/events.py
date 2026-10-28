import json
import os
from pathlib import Path


def emit(plugin, phase):
    path = os.environ.get('BYUL_B_PLUGIN_EVENTS')
    if path:
        with Path(path).open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'plugin': plugin, 'phase': phase}) + '\n')
