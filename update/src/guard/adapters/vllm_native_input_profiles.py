"""Exact installed source candidates, selected by image and interpreter data."""
from copy import deepcopy
import json
from pathlib import Path
import re
import sys

PROFILES=Path(__file__).with_name('assets')/'vllm_native_input_profiles.json'


def select(image, *, runtime=None):
    runtime=runtime or sys.implementation.name+'-'+'.'.join(map(str,sys.version_info[:3]))
    try:
        data=json.loads(PROFILES.read_bytes())
        if data['schema']!='guard-native-input-source-candidates/1':raise ValueError
        rows=[row for row in data['profiles']if row['image']==image and row['runtime']==runtime]
        if len(rows)!=1:raise ValueError
        profile,=rows
        if (profile['runtime_qualified']is not False or profile['producer_registered']is not False
                or not profile['sources']or not isinstance(profile['sources'],dict)):
            raise ValueError
        for name,pin in profile['sources'].items():
            if (type(name)is not str or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]*',name)
                    or type(pin['bytes'])is not int or not 0<pin['bytes']<=262144
                    or not re.fullmatch('[0-9a-f]{64}',pin['sha256'])
                    or not pin['functions'] or len(set(pin['functions']))!=len(pin['functions'])
                    or set(pin['body_code_sha256'])!=set(pin['functions'])
                    or not set(pin['hooks'])<=set(pin['functions'])):raise ValueError
        return deepcopy(profile)
    except (OSError,ValueError,KeyError,TypeError):
        raise ValueError('NATIVE_INPUT_PROFILE_UNAVAILABLE')from None
