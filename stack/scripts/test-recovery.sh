#!/bin/sh
set -eu
# Run after test-stack.sh. Restart the same volumes, then query the original trace.
docker compose restart collector prometheus tempo loki grafana
docker compose run --rm -e REPORT_PATH=/artifacts/restart.json tests python e2e.py --verify-restart
# The backend is unavailable while a sampled trace is confirmed in the persistent queue.
docker compose stop tempo loki
trap 'docker compose start tempo loki collector' EXIT
docker compose run --rm -e REPORT_PATH=/artifacts/enqueue.json tests python e2e.py --enqueue
docker compose up -d --force-recreate collector
docker compose start tempo loki
docker compose run --rm -e REPORT_PATH=/artifacts/queue-recovery.json tests python e2e.py --verify-queue
docker compose stop collector
docker compose run --rm -e REPORT_PATH=/artifacts/collector-firing.json tests python e2e.py --collector-firing
docker compose start collector
docker compose run --rm -e REPORT_PATH=/artifacts/collector-resolved.json tests python e2e.py --collector-resolved
