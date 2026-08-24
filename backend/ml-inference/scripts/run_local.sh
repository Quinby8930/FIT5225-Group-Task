#!/usr/bin/env sh
set -eu

export PYTHONPATH="${PYTHONPATH:-.}"
export INFERENCE_BACKEND="${INFERENCE_BACKEND:-mock}"
export INTERNAL_API_KEY="${INTERNAL_API_KEY:-local-demo-secret}"
exec python -m app
