"""Explicit request correlation over the existing owned input transport."""
import json
from pathlib import Path
import re

from .kubernetes_http import KubernetesHTTP,INPUT_VALIDATION_PROFILE,KubernetesBindingError

READER=Path(__file__).with_name('assets')/'native_input_read_v1.py.txt'
LABELS=frozenset({'before','token_id','length','after_token_id','after_length','challenge-0','after-0'})


class KubernetesNativeInputHTTP(KubernetesHTTP):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        if self.profile!=INPUT_VALIDATION_PROFILE:raise KubernetesBindingError()
        self._native_ids=set()
        self._native_pending=set()

    def declare_request_id(self,value):
        if(type(value)is not str or not re.fullmatch(r'byul-input-[0-9a-f]{32}-[A-Za-z0-9_-]{1,40}',value)
                or value in self._native_ids or len(self._native_ids)>=32):raise KubernetesBindingError()
        self._native_ids.add(value)
        self._native_pending.add(value)

    def _input_validation_request(self,request):
        headers=request.get('headers')if type(request)is dict else None
        if type(headers)is not dict or set(headers)!={'X-Request-Id'}or headers['X-Request-Id']not in self._native_pending:
            raise KubernetesBindingError()
        # The original body/path/method/bounds checks still apply unchanged.
        super()._input_validation_request({**request,'headers':{}})
        self._native_pending.remove(headers['X-Request-Id'])

    def native_input_records(self,prepared):
        self._verify();engine=self.binding['engine']
        spec={key:prepared[key]for key in('session','settings_sha256','assets')}
        payload=json.dumps(spec,separators=(',',':')).encode()
        value=self._call([f"--namespace={engine['namespace']}",'exec','-i',engine['pod'],
            f"--container={engine['container']}",'--','python3','-I','-S','-c',READER.read_text(encoding='utf-8')],
            payload=payload,cap=8*1024**2,budget=30)
        self._verify()
        if(type(value)is not dict or value.get('schema')!='guard-native-input-owned-read/1'
                or value.get('session')!=prepared['session']or value.get('settings_sha256')!=prepared['settings_sha256']
                or value.get('complete')is not True):raise KubernetesBindingError()
        return value
