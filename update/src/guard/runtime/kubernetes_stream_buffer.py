"""LIF04 explicit read-control draft; client timing is not serving buffer cost."""
from copy import deepcopy

PROFILE='kubernetes-owned-stream-buffer-v1'
PRODUCT_REGISTERED=True


def normalize_read_profiles(value):
    """Reuse current slow-reader bounds; no defaults or debug route activation."""
    try:
        if type(value) is not dict or set(value)!={'normal','slow','slow-close'} or value['normal'] is not None:raise ValueError
        for name in ('slow','slow-close'):
            profile=value[name]
            if (type(profile) is not dict or set(profile)!={'profile','chunk_bytes','interval_ms','close_after_reads'}
                    or profile['profile']!='slow-reader-v1'
                    or any(type(profile[k]) is not int or not 1<=profile[k]<=maximum for k,maximum in
                        (('chunk_bytes',4096),('interval_ms',250),('close_after_reads',32)))):raise ValueError
        if value['slow-close']['close_after_reads']>=value['slow']['close_after_reads']:raise ValueError
        return deepcopy(value)
    except (KeyError,TypeError,ValueError):raise ValueError('STREAM_BUFFER_READ_PROFILE_INVALID') from None
