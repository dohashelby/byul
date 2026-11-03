"""REQ-16 lab 문맥용 변형을 만든다. 엔진 컨테이너 안에서 표준 라이브러리만으로 돈다.

lora: 모델 config에 맞는 rank 8 LoRA 두 벌(내용만 다르고 형식은 같다)을 seed로 만든다.
weights: 모델 폴더를 복사하고 지정한 텐서 하나만 1.0으로 바꾼 두 번째 가중치 세대를 만든다.
아무것도 내려받지 않는다. 출력은 EXAMPLE 시험 입력이며 요약 JSON만 표준 출력으로 낸다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import struct

SCHEMA = 'byul-gpu-a-lab-variants/1'
DTYPES = {'F16': ('e', 2), 'BF16': (None, 2), 'F32': ('f', 4)}
ONE = {'F16': struct.pack('<e', 1.0), 'BF16': struct.pack('<f', 1.0)[2:], 'F32': struct.pack('<f', 1.0)}


def _sha(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _header(path):
    with open(path, 'rb') as stream:
        size = struct.unpack('<Q', stream.read(8))[0]
        if not 0 < size <= 100 * 1024**2:
            raise ValueError('LAB_VARIANT_SAFETENSORS_INVALID')
        return size, json.loads(stream.read(size))


def write_safetensors(path, tensors):
    """tensors: name -> (dtype, shape, raw bytes)."""
    header = {'__metadata__': {'format': 'pt'}}; offset = 0
    for name, (dtype, shape, raw) in tensors.items():
        header[name] = {'dtype': dtype, 'shape': list(shape), 'data_offsets': [offset, offset + len(raw)]}
        offset += len(raw)
    encoded = json.dumps(header, separators=(',', ':')).encode()
    encoded += b' ' * (-len(encoded) % 8)
    with open(path, 'xb') as stream:
        stream.write(struct.pack('<Q', len(encoded)) + encoded)
        for _, _, raw in tensors.values():
            stream.write(raw)


def lora(model_dir, out_dir, seed, rank=8, alpha=16, names=('a', 'b')):
    config = json.loads((Path(model_dir) / 'config.json').read_text(encoding='utf-8'))
    hidden, layers = config['hidden_size'], config['num_hidden_layers']
    head_dim = config.get('head_dim') or hidden // config['num_attention_heads']
    shapes = {'q_proj': config['num_attention_heads'] * head_dim, 'v_proj': config['num_key_value_heads'] * head_dim}
    result = {}
    for index, name in enumerate(names):
        rng = random.Random(f'{seed}:{index}')
        values = lambda count: struct.pack(f'<{count}e', *(rng.uniform(-0.02, 0.02) for _ in range(count)))
        tensors = {}
        for layer in range(layers):
            for module, out in shapes.items():
                key = f'base_model.model.model.layers.{layer}.self_attn.{module}'
                tensors[key + '.lora_A.weight'] = ('F16', (rank, hidden), values(rank * hidden))
                tensors[key + '.lora_B.weight'] = ('F16', (out, rank), values(out * rank))
        folder = Path(out_dir) / name; folder.mkdir(parents=True)
        (folder / 'adapter_config.json').write_text(json.dumps({'peft_type': 'LORA', 'task_type': 'CAUSAL_LM', 'r': rank,
            'lora_alpha': alpha, 'lora_dropout': 0.0, 'bias': 'none', 'target_modules': sorted(shapes),
            'fan_in_fan_out': False, 'inference_mode': True,
            'base_model_name_or_path': config.get('_name_or_path') or 'local-model'}, sort_keys=True), encoding='utf-8')
        write_safetensors(folder / 'adapter_model.safetensors', tensors)
        result[name] = {file.name: _sha(file) for file in sorted(folder.iterdir())}
    return {'schema': SCHEMA, 'kind': 'lora', 'data_origin': 'EXAMPLE', 'seed': seed, 'rank': rank, 'variants': result}


def weights(model_dir, out_dir, tensor='model.norm.weight'):
    source, target = Path(model_dir), Path(out_dir)
    target.mkdir(parents=True)
    changed = None
    for file in sorted(source.iterdir()):
        if not file.is_file():
            continue
        if file.suffix != '.safetensors':
            shutil.copyfile(file, target / file.name); continue
        size, header = _header(file)
        entry = header.get(tensor)
        if entry is None:
            shutil.copyfile(file, target / file.name); continue
        if entry['dtype'] not in ONE or changed is not None:
            raise ValueError('LAB_VARIANT_TENSOR_UNSUPPORTED')
        start, end = (8 + size + v for v in entry['data_offsets'])
        with open(file, 'rb') as reader, open(target / file.name, 'xb') as writer:
            shutil.copyfileobj(reader, writer, 1 << 20)
            reader.seek(start); before = reader.read(end - start)
            replacement = ONE[entry['dtype']] * ((end - start) // len(ONE[entry['dtype']]))
            if before == replacement:
                raise ValueError('LAB_VARIANT_TENSOR_ALREADY_ONE')
            writer.seek(start); writer.write(replacement)
        changed = {'file': file.name, 'tensor': tensor, 'dtype': entry['dtype'],
            'before_sha256': hashlib.sha256(before).hexdigest(), 'after_sha256': hashlib.sha256(replacement).hexdigest()}
    if changed is None:
        raise ValueError('LAB_VARIANT_TENSOR_MISSING')
    return {'schema': SCHEMA, 'kind': 'weights', 'data_origin': 'EXAMPLE', 'changed': changed,
        'files_sha256': {file.name: _sha(file) for file in sorted(target.iterdir())}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='kind', required=True)
    one = sub.add_parser('lora'); one.add_argument('--model-dir', required=True); one.add_argument('--out', required=True)
    one.add_argument('--seed', type=int, required=True)
    two = sub.add_parser('weights'); two.add_argument('--model-dir', required=True); two.add_argument('--out', required=True)
    two.add_argument('--tensor', default='model.norm.weight')
    args = parser.parse_args(argv)
    os.umask(0o077)
    result = (lora(args.model_dir, args.out, args.seed) if args.kind == 'lora'
        else weights(args.model_dir, args.out, args.tensor))
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
