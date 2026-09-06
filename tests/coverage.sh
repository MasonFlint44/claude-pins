#!/usr/bin/env bash
# Line and branch coverage of claude_pins for the whole suite, subprocesses included.
# Needs uv (https://docs.astral.sh/uv/); everything else comes from the dev dependency group.
set -eu
cd "$(dirname "$0")/.."
uv sync --quiet --group dev
site=$(uv run --quiet python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
export COVERAGE_PROCESS_START="$PWD/pyproject.toml"
export COVERAGE_FILE="$PWD/.coverage"
export PYTHONPATH="$site:$PWD/tests/coverage_hook${PYTHONPATH:+:$PYTHONPATH}"
rm -f .coverage .coverage.*
uv run --quiet coverage run -m unittest -q "$@"
uv run --quiet coverage combine --quiet
uv run --quiet coverage report
