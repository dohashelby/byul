"""Small fixed OpenAI-compatible input pairs for the selected image/profile."""
from copy import deepcopy
from . import vllm_native_input_rejection as original


def fixtures(*,engine,version,image,pairs,model,vocab_size,limit):
    original.profile(engine,version,image)  # Exact image and interpreter only.
    supported={('POST /v1/completions','token_ids'),('POST /v1/completions','text'),('POST /v1/chat/completions','text')}
    if(any(tuple(p)not in supported for p in pairs)or type(model)is not str or not model
            or type(vocab_size)is not int or not 1<vocab_size<2**31 or type(limit)is not int or not 1<=limit<=512):
        raise ValueError('INPUT_MATRIX_ENGINE_PAIR_UNSUPPORTED')
    result=[]
    for endpoint,format_name in pairs:
        path=endpoint.partition(' ')[2];base={'model':model,'max_tokens':1}
        if path=='/v1/chat/completions':
            control={**base,'messages':[{'role':'user','content':'hello'}]}
            length={**base,'messages':[{'role':'user','content':'a '*(limit+2)}]}
            cases={'length':length}
        elif format_name=='text':
            control={**base,'prompt':'hello'};cases={'length':{**base,'prompt':'a '*(limit+2)}}
        else:
            control={**base,'prompt':[0]}
            cases={'token_id':{**base,'prompt':[vocab_size]},'length':{**base,'prompt':[0]*(limit+1)}}
        result.append({'endpoint':endpoint,'path':path,'format':format_name,'control':control,'cases':cases})
    return result
