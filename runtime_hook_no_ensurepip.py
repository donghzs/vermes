"""Runtime hook: prevent ensurepip from running."""
import os
import sys

# Set environment variable to prevent ensurepip
os.environ['PIP_NO_ENSUREPIP'] = '1'

# Monkey-patch ensurepip to do nothing
import importlib
try:
    ensurepip = importlib.import_module('ensurepip')
    ensurepip._main = lambda *a, **kw: None
    ensurepip._bootstrap = lambda *a, **kw: None
except Exception:
    pass

# Also prevent subprocess from running ensurepip
_original_popen = None
try:
    import subprocess
    _original_popen = subprocess.Popen.__init__
    
    def _patched_popen(self, args, *a, **kw):
        if isinstance(args, (list, tuple)):
            args_str = ' '.join(str(x) for x in args)
        else:
            args_str = str(args)
        if 'ensurepip' in args_str:
            # Silently skip ensurepip calls
            raise OSError('ensurepip blocked by runtime hook')
        return _original_popen(self, args, *a, **kw)
    
    subprocess.Popen.__init__ = _patched_popen
except Exception:
    pass
