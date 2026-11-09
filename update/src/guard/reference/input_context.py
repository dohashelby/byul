"""Offline tiny source-CPU input fixture; no model weights or downloads."""
from pathlib import Path
import hashlib
def build_context(folder):
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import GPT2Config, TokenizersBackend
    from vllm.config import ModelConfig, VllmConfig, DeviceConfig
    from vllm.v1.engine.input_processor import InputProcessor
    folder.mkdir()
    GPT2Config(vocab_size=32, n_embd=4, n_layer=1, n_head=1, n_positions=16,
               bos_token_id=1, eos_token_id=2, architectures=['GPT2LMHeadModel']).save_pretrained(folder)
    vocab = {'[UNK]': 0, '[BOS]': 1, '[EOS]': 2, 'hello': 3, 'world': 4, 'user': 5, 'assistant': 6}
    vocab.update({f'word{i}': i for i in range(7, 32)})
    token = Tokenizer(WordLevel(vocab, unk_token='[UNK]'))
    token.pre_tokenizer = Whitespace()
    tokenizer = TokenizersBackend(tokenizer_object=token, unk_token='[UNK]', bos_token='[BOS]', eos_token='[EOS]')
    tokenizer.chat_template = "{% for m in messages %}{{ m['role'] }} {{ m['content'] }} {% endfor %}"
    tokenizer.save_pretrained(folder)
    model_config = ModelConfig(model=str(folder), tokenizer=str(folder), dtype='float32',
                               max_model_len=16, enforce_eager=True, enable_prompt_embeds=True)
    config = VllmConfig(model_config=model_config, device_config=DeviceConfig(device='cpu'))
    return InputProcessor(config)

SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
