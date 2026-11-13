#!/usr/bin/env bash
# Linux version of start-server.ps1. Override with env vars, e.g.:
#   CPU_MOE=30 CTX=131072 ./start-server.sh
set -euo pipefail
KIT_DIR="$(cd "$(dirname "$0")" && pwd)"

CTX="${CTX:-65536}"
CPU_MOE="${CPU_MOE:-99}"
CACHE_TYPE="${CACHE_TYPE:-q8_0}"
THREADS="${THREADS:-8}"
PORT="${PORT:-8080}"
MODEL="${MODEL:-$(find "$KIT_DIR/models" -name '*.gguf' ! -name '*mmproj*' | sort | head -n1)}"

[ -n "$MODEL" ] || { echo "No .gguf in $KIT_DIR/models; set MODEL=/path/to/model.gguf" >&2; exit 1; }
SERVER="$(command -v llama-server || echo "$KIT_DIR/llama.cpp/llama-server")"
[ -x "$SERVER" ] || { echo "llama-server not found (see README)" >&2; exit 1; }

echo "Model: $MODEL | ctx $CTX | experts-on-CPU $CPU_MOE | KV $CACHE_TYPE | http://127.0.0.1:$PORT"
exec "$SERVER" -m "$MODEL" --alias qwen3.6 -c "$CTX" \
  -ngl 99 --n-cpu-moe "$CPU_MOE" \
  -fa on --cache-type-k "$CACHE_TYPE" --cache-type-v "$CACHE_TYPE" \
  -t "$THREADS" --jinja \
  --temp 0.6 --top-p 0.95 --top-k 20 --min-p 0 \
  --host 127.0.0.1 --port "$PORT"
