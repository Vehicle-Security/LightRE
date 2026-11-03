#!/bin/sh
# Opt-in integration check: a fresh Ubuntu image, real downloads and Ghidra.
set -eu

verify_service() {
  cd /opt/LightRE
  tools/bin/codex --version
  printf '%s\n' 'int lightre_add(int a, int b) { return a + b; }' > /tmp/target.c
  mkdir -p /tmp/target/lib/arm64-v8a
  clang --target=aarch64-linux-android23 -nostdlib -shared -fuse-ld=lld /tmp/target.c -o /tmp/target/lib/arm64-v8a/target.so
  # Credentials and model execution are outside this dependency/startup check.
  LIGHTRE_CODEX_BIN=/bin/false ./lightre /tmp/target --workspace /tmp/lightre-container-workspace --host 127.0.0.1 --port 8765 > /tmp/lightre-service.log 2>&1 &
  service_pid=$!
  trap 'kill "$service_pid" 2>/dev/null || true' EXIT HUP INT TERM
  attempts=0
  while ! curl --silent --fail http://127.0.0.1:8765/api/health > /tmp/lightre-health.json; do
    attempts=$((attempts + 1))
    if [ "$attempts" -ge 180 ] || ! kill -0 "$service_pid" 2>/dev/null; then
      cat /tmp/lightre-service.log
      find /tmp/lightre-container-workspace -name headless.log -exec cat '{}' \;
      exit 1
    fi
    sleep 1
  done
  curl --silent --fail http://127.0.0.1:8765/ui/ > /dev/null
  .venv/bin/python - <<'PY'
import json
import sqlite3
from pathlib import Path

health = json.loads(Path('/tmp/lightre-health.json').read_text())
assert health['status'] == 'ok', health
workspace = Path(health['workspace'])
with sqlite3.connect(workspace / 'index.sqlite') as conn:
    metadata = dict(conn.execute('SELECT key, value FROM metadata'))
    assert metadata['schema_version'] == '2' and metadata['state'] == 'ready', metadata
    assert conn.execute('SELECT COUNT(*) FROM functions').fetchone()[0] > 0
    assert conn.execute('SELECT COUNT(*) FROM assembly').fetchone()[0] > 0
assert (workspace / 'binary/original.so').read_bytes() == Path('/tmp/target/lib/arm64-v8a/target.so').read_bytes()
selection = json.loads((workspace.parent / 'target.json').read_text())
assert selection['binary'] == 'lib/arm64-v8a/target.so', selection
assert list((workspace / 'source/src').glob('*.c'))
print('PASS: clean-container bootstrap, Codex binary, real Ghidra export, workspace, HTTP API and UI')
PY
}

case "${1:-}" in
  --inside)
    for tool in curl uv python3 java mvn codex clang; do
      if command -v "$tool" >/dev/null 2>&1; then
        printf '%s\n' "Expected a clean image, found $tool" >&2
        exit 1
      fi
    done
    cd /opt/LightRE
    ./lightre --prepare
    verify_service
    exit
    ;;
  --verify)
    verify_service
    exit
    ;;
esac

root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
temporary="$(mktemp -d)"
container=""
cleanup() {
  if [ -n "$container" ]; then docker rm -f "$container" >/dev/null 2>&1 || true; fi
  rm -rf "$temporary"
}
trap cleanup EXIT HUP INT TERM
tar -C "$root" --exclude='__pycache__' --exclude='*.pyc' -cf "$temporary/source.tar" \
  lightre pyproject.toml uv.lock README.md src tests/test_clean_container.sh
container="$(docker create --workdir /opt/LightRE ubuntu:24.04 sh -c \
  'tar -xf /tmp/source.tar -C /opt/LightRE && exec sh tests/test_clean_container.sh --inside')"
docker cp "$temporary/source.tar" "$container:/tmp/source.tar"
docker start --attach "$container"
test "$(docker inspect --format '{{.State.ExitCode}}' "$container")" = 0
