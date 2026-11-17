"""Chain the original sitecustomize and extend only the private rank namespace."""

BOOTSTRAP = b'''"""Separate passive response trace after the original SDK bootstrap."""
def _byul_response_start():
    import importlib
    import importlib.machinery
    from pathlib import Path
    import sys
    current = sys.modules[__name__]
    current.__dict__.pop('_byul_response_start', None)
    own = Path(__file__).resolve().parent
    search = []
    for item in sys.path:
        try:
            same = type(item) is str and Path(item or '.').resolve() == own
        except OSError:
            same = False
        if not same:
            search.append(item)
    sys.path[:] = search
    original = importlib.machinery.PathFinder.find_spec('sitecustomize', search)
    if original is not None and original.loader is not None:
        current.__file__, current.__loader__, current.__spec__ = original.origin, original.loader, original
        current.__package__, current.__cached__ = original.parent, original.cached
        original.loader.exec_module(current)
    try:
        package = sys.modules.get('_byul_rank_ipc.adapters')
        if package is None or type(package.__path__) is not list:
            raise ValueError('RANK_RESPONSE_ORIGINAL_SDK_REQUIRED')
        extension = str(own / 'guard' / 'adapters')
        if extension in package.__path__:
            raise ValueError('RANK_RESPONSE_NAMESPACE_IN_USE')
        package.__path__.append(extension)
        module = importlib.import_module('_byul_rank_ipc.adapters.vllm_rank_response_runtime')
        module.install()
    except Exception:
        sys.stderr.write('RANK_RESPONSE_OBSERVER_UNAVAILABLE\\n')
_byul_response_start()
'''
