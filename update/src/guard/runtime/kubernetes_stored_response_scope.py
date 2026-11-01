"""REQ06 in-memory probe scope; never an authenticated object owner.

The closed transport binds creation claims and selected generations. Actual
principal/policy, store producer and worker effects remain separate evidence.
"""
from copy import deepcopy
import re,uuid
from guard.models import sha256_hex

PRODUCT_REGISTERED=True
OPERATIONS=frozenset({'read','cancel','previous','overwrite'})
ORIGINS=frozenset({'gateway','direct'})
ROLES=frozenset({'A','B'})
OBJECT_KINDS=frozenset({'read','cancel-normal','cancel-target','previous','overwrite'})
INVALIDATIONS=frozenset({'target_replaced','restart','window_expired','cleanup_unverified','binding_unverified'})


def normalize_scope(value):
    """Explicit names only; no credential lookup or network/owner assertion."""
    try:
        if (type(value) is not dict or set(value)!={'session','origins','operations','roles'}
                or type(value['session']) is not str or re.fullmatch('[0-9a-f]{32}',value['session']) is None):raise ValueError
        for key,allowed in (('origins',ORIGINS),('operations',OPERATIONS)):
            items=value[key]
            if type(items) is not list or not 1<=len(items)<=len(allowed) or any(type(x) is not str or x not in allowed for x in items) or len(set(items))!=len(items):raise ValueError
        if type(value['roles']) is not dict or set(value['roles'])!=ROLES:raise ValueError
        names=list(value['roles'].values())
        if any(type(n) is not str or re.fullmatch('[A-Z_][A-Z0-9_]{0,63}',n) is None for n in names) or len(set(names))!=2:raise ValueError
        return deepcopy(value)
    except (KeyError,ValueError,TypeError):raise ValueError('STORED_RESPONSE_SCOPE_INVALID') from None


class OwnedResponseScope:
    """Limit a future probe to IDs named by this run, kept transient in memory.

    Matching an ID limits target selection. It cannot prove store persistence,
    a principal/owner, a permission decision or a background task's termination.
    """
    def __init__(self,value,*,run_nonce=None):
        self._scope=normalize_scope(value);self._receipts=set();self._invalidated=None
        self._nonce=uuid.uuid4().hex if run_nonce is None else run_nonce
        if type(self._nonce) is not str or re.fullmatch('[0-9a-f]{32}',self._nonce) is None:raise ValueError('STORED_RESPONSE_SCOPE_INVALID')

    def _key(self,origin,role,kind):
        if (self._invalidated is not None or type(origin) is not str or origin not in self._scope['origins']
                or type(role) is not str or role not in ROLES or type(kind) is not str
                or kind not in OBJECT_KINDS or kind.split('-')[0] not in self._scope['operations']):raise ValueError('STORED_RESPONSE_SCOPE_UNAVAILABLE')
        return origin,role,kind

    def expected_id(self,origin,role,kind):
        origin,role,kind=self._key(origin,role,kind)
        return self._id(origin,role,kind)

    def _id(self,origin,role,kind):
        return 'resp_byul_req06_'+self._nonce+'_'+origin+'_'+role+'_'+kind

    def has_creation_claim(self,origin,role,kind):
        return self._key(origin,role,kind) in self._receipts

    def remember_creation_claim(self,origin,role,kind,returned_id):
        key=self._key(origin,role,kind)
        if type(returned_id) is not str or returned_id!=self.expected_id(*key) or key in self._receipts:
            raise ValueError('STORED_RESPONSE_CREATION_CLAIM_INVALID')
        self._receipts.add(key)

    def require_created(self,origin,role,kind,object_id):
        key=self._key(origin,role,kind)
        if key not in self._receipts or type(object_id) is not str or object_id!=self.expected_id(*key):
            raise ValueError('STORED_RESPONSE_NOT_THIS_PROBE_OBJECT')
        return object_id

    def invalidate(self,reason):
        if type(reason) is not str or reason not in INVALIDATIONS:raise ValueError('STORED_RESPONSE_INVALIDATION_INVALID')
        if self._invalidated is None:self._invalidated=reason

    def report(self):
        # Keep earlier creation facts after invalidation; never persist raw IDs.
        return {'product_registered':True,'session_sha256':sha256_hex(self._scope['session'].encode()),'run_nonce_sha256':sha256_hex(self._nonce.encode()),
            'invalidated':self._invalidated,'creation_claims':[{'origin':o,'role_alias':r,'operation':k,
                'object_id_sha256':sha256_hex(self._id(o,r,k).encode())}
                for o,r,k in sorted(self._receipts)],'producer_authenticated':False,'object_owner_verified':False,
            'store_effect_verified':False,'whole_scope_complete':False}
