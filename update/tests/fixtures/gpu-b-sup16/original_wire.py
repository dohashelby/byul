"""Original selected source-byte wire functions with EXAMPLE path constants."""

import base64,hashlib,json,zlib

MAX_RAW=6*1024**2
MAX_WIRE=20000
SCHEMA='SUP16-original-result-filebytes-zlib/2'

WRAPPER_PATH='/EXAMPLE/current-wrapper-finally.json'
RECEIPT_PATH='/EXAMPLE/outer-archive-receipt.json'

def require(ok,code):
    if not ok:raise ValueError(code)

def pairs(rows):
    d={}
    for k,v in rows:
        require(k not in d,'WIRE_DUPLICATE_KEY');d[k]=v
    return d

def pack(raw,sources):
    raw=json.dumps({'result_json_base64':base64.b64encode(raw).decode('ascii'),'source_files':sources},separators=(',',':')).encode()
    require(type(raw) is bytes and 0<len(raw)<=MAX_RAW,'WIRE_RAW_BOUND')
    d={'schema':SCHEMA,'raw_bytes':len(raw),'raw_sha256':hashlib.sha256(raw).hexdigest(),
       'zlib_base64':base64.b64encode(zlib.compress(raw,9)).decode('ascii')}
    wire=json.dumps(d,separators=(',',':'))
    require(len(wire.encode('ascii'))<=MAX_WIRE,'WIRE_OUTPUT_BOUND')
    return wire

def unpack_bundle(wire):
    require(type(wire) is str and len(wire.encode('utf-8'))<=MAX_WIRE,'WIRE_OUTPUT_BOUND')
    d=json.loads(wire,object_pairs_hook=pairs)
    require(type(d) is dict and set(d)=={'schema','raw_bytes','raw_sha256','zlib_base64'}
            and d['schema']==SCHEMA,'WIRE_SCHEMA')
    require(type(d['raw_bytes']) is int and 0<d['raw_bytes']<=MAX_RAW,'WIRE_RAW_BOUND')
    blob=base64.b64decode(d['zlib_base64'],validate=True)
    z=zlib.decompressobj();raw=z.decompress(blob,d['raw_bytes']+1)
    require(z.eof and not z.unused_data and not z.unconsumed_tail
            and len(raw)==d['raw_bytes'] and hashlib.sha256(raw).hexdigest()==d['raw_sha256'],
            'WIRE_EXACT_ORIGINAL_BYTES')
    bundle=json.loads(raw,object_pairs_hook=pairs)
    require(type(bundle) is dict and set(bundle)=={'result_json_base64','source_files'},'WIRE_BUNDLE')
    result_raw=base64.b64decode(bundle['result_json_base64'],validate=True)
    result=json.loads(result_raw,object_pairs_hook=pairs)
    require(type(result) is dict and set(result)=={'wrapper','archive_receipt'},'WIRE_RESULT')
    files=bundle['source_files']
    expected={WRAPPER_PATH}|({RECEIPT_PATH} if result['archive_receipt'] is not None else set())
    require(type(files) is dict and set(files)==expected,'WIRE_ORIGINAL_SOURCE_PATHS')
    for path,value in files.items():
        require(type(value) is dict and set(value)=={'bytes','sha256','raw_base64'}
                and type(value['bytes']) is int and 0<value['bytes']<3*1024**2,'WIRE_ORIGINAL_FILE_BOUND')
        original=base64.b64decode(value['raw_base64'],validate=True)
        require(len(original)==value['bytes'] and hashlib.sha256(original).hexdigest()==value['sha256'],
                'WIRE_ORIGINAL_FILE_BYTES')
        source_value=json.loads(original,object_pairs_hook=pairs)
        field='wrapper' if path==WRAPPER_PATH else 'archive_receipt'
        canonical=lambda v:json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)
        require(canonical(source_value)==canonical(result[field]),'WIRE_ORIGINAL_FILE_RESULT_MATCH')
    return bundle

def unpack(wire):
    return base64.b64decode(unpack_bundle(wire)['result_json_base64'],validate=True)

def require(condition, code):
    if not condition:
        raise ValueError(code)
