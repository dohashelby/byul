from .events import emit
emit('byul-task-mismatch', 'import')
from .runtime import Endpoint


def endpoint():
    return Endpoint('byul-task-mismatch', ('embed',))
