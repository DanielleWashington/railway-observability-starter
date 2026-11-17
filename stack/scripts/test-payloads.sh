#!/bin/sh
# Requires the local stack started by scripts/test-stack.sh; preserves its data.
set -eu
docker compose run --build --rm --no-deps \
  -e SECURITY_MODE=payloads \
  -e COLLECTOR_METRICS_URL=http://collector:8888/metrics \
  tests python /tests/security.py
docker compose run --rm --no-deps \
  -e SECURITY_MODE=backend-limits \
  -e COLLECTOR_METRICS_URL=http://collector:8888/metrics \
  tests python /tests/security.py
