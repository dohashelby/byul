from .events import emit
emit('byul-approved', 'import')
from .runtime import Endpoint, Processor as BaseProcessor

def general(): emit('byul-approved', 'general_hook')
def endpoint(): return Endpoint('byul-approved')
def io_factory():
    emit('byul-approved', 'io_factory')
    return 'byul_b_cpu_probe.approved.Processor'
class Processor(BaseProcessor): plugin_name = 'byul-approved'
from vllm.v1.sample.logits_processor.interface import LogitsProcessor
class Logits(LogitsProcessor):
    """Valid V1 interface; this bundle observes loading, not model execution."""
    def __init__(self, config, device, is_pin_memory): pass
    def apply(self, logits): return logits
    def is_argmax_invariant(self): return True
    def update_state(self, batch_update): pass
