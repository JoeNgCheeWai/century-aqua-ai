"""
Test package setup.

ENVIRONMENT SHIM (only active when needed): on some Windows machines Application
Control / Smart App Control blocks the native `jiter` file that the real
`anthropic` package imports, so `import anthropic` fails before any test runs.
When (and only when) that happens, a tiny stand-in `anthropic` module is
installed so the tests, which never call the real API, can still run. On a
machine where the real package imports fine (Linux, Render, CI) nothing is
replaced. Tests that need a model use their own fakes.
"""

import sys
import types
import warnings

try:
    import anthropic  # noqa: F401
except Exception as _exc:  # ImportError, or a blocked-DLL error
    for _name in [n for n in sys.modules if n == "anthropic" or n.startswith("anthropic.") or n == "jiter" or n.startswith("jiter.")]:
        del sys.modules[_name]

    class AsyncAnthropic:  # minimal stand-in; tests replace _client with their own fakes
        def __init__(self, *args, **kwargs):
            raise RuntimeError("stub anthropic: the real API must not be called from tests")

    _stub = types.ModuleType("anthropic")
    _stub.AsyncAnthropic = AsyncAnthropic
    _stub.__stub__ = True
    sys.modules["anthropic"] = _stub
    warnings.warn(f"real 'anthropic' could not be imported ({type(_exc).__name__}); using a test stub", stacklevel=1)