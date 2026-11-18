"""Only exact declared bounded bodies, over the original owned HTTP transport."""
from copy import deepcopy
from urllib.parse import urlsplit
from guard.models import canonical_json,sha256_hex
from .kubernetes_native_input import KubernetesNativeInputHTTP
from .kubernetes_http import KubernetesBindingError


class KubernetesInputMatrixHTTP(KubernetesNativeInputHTTP):
    def declare_matrix(self,path,bodies):
        if(hasattr(self,'_matrix_bodies')or type(bodies)is not list or not 2<=len(bodies)<=3
                or any(type(b)is not dict or b.get('model')!=self.api_model or len(canonical_json(b))>4096 for b in bodies)):
            raise KubernetesBindingError()
        self.declare_input_contract({'path':path,'control':bodies[0],'prompt_tokens':None,'fields':{}})
        self._matrix_bodies={sha256_hex(canonical_json(b))for b in bodies};self._matrix_path=path

    def _input_validation_request(self,request):
        try:
            headers=request['headers'];body=request['body'];path=self._matrix_path
            if(type(headers)is not dict or set(headers)!={'X-Request-Id'}or headers['X-Request-Id']not in self._native_pending
                    or request.get('id')!='func|POST_'+path or request.get('method')!='POST'
                    or urlsplit(request['url']).path!=path or request.get('mode','json')!='json'
                    or type(body)is not dict or len(canonical_json(body))>4096
                    or sha256_hex(canonical_json(body))not in self._matrix_bodies):raise ValueError
            self._native_pending.remove(headers['X-Request-Id'])
        except(KeyError,ValueError,TypeError,AttributeError):raise KubernetesBindingError()from None
