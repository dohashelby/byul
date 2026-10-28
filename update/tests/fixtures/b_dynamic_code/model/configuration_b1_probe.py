"""Harmless owned custom-config fixture; no weights, subprocess or network."""
import json
import os
from pathlib import Path
from transformers import PreTrainedConfig


def _mark(phase):
    path=os.environ.get('BYUL_B_DYNAMIC_EVENTS')
    if path:
        with Path(path).open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'phase':phase})+'\n')


_mark('import')


class B1ProbeConfig(PreTrainedConfig):
    model_type='byul_cpu_probe_dynamic'

    def __init__(self,**kwargs):
        _mark('constructor')
        super().__init__(**kwargs)
