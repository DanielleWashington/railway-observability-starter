#!/bin/sh
# Preserves volumes for inspection. Use a unique Compose project for an empty-volume run.
set -eu
python3 scripts/bootstrap.py
docker compose --profile test build collector prometheus tempo loki grafana downstream api alert-sink tests
docker compose run --rm --no-deps --entrypoint /otelcol-contrib collector validate --config=/etc/otelcol-contrib/config.yaml
docker compose run --rm --no-deps --entrypoint /bin/sh prometheus -c 'sed "s/\${COLLECTOR_METRICS_HOST}/collector/g" /etc/prometheus/prometheus.yml >/tmp/check.yml && promtool check config /tmp/check.yml'
docker compose run --rm --no-deps loki -config.file=/etc/loki/config.yaml -config.expand-env=true -verify-config=true
docker compose up -d collector prometheus tempo loki grafana downstream api alert-sink
docker compose run --rm tests
docker compose run --rm -e REPORT_PATH=/artifacts/alerts.json tests python e2e.py --alerts
