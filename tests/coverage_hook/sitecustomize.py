"""Put this directory on PYTHONPATH (tests/coverage.sh does) and every Python process started
during the test run records coverage, including the ``bin/pins`` subprocesses.

``pins`` ends by exec-ing ``claude``, which would discard that process's data before coverage
could write it, so exec is wrapped to save first.
"""
import os

try:
    import coverage
except ImportError:  # a different interpreter without the dev venv on its path
    coverage = None

if coverage is not None and os.environ.get("COVERAGE_PROCESS_START"):
    coverage.process_startup()
    _execv = os.execv

    def _saving_execv(path, args):
        cov = coverage.Coverage.current()
        if cov is not None:
            cov.stop()
            cov.save()
        _execv(path, args)

    os.execv = _saving_execv
