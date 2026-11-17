#!/bin/sh
set -eu
host=${COLLECTOR_METRICS_HOST:-collector}
case "$host" in *[!a-zA-Z0-9.-]*|'') echo 'Invalid COLLECTOR_METRICS_HOST' >&2; exit 1;; esac
sed "s/\${COLLECTOR_METRICS_HOST}/$host/g" /etc/prometheus/prometheus.yml > /tmp/prometheus.yml
exec /bin/prometheus --config.file=/tmp/prometheus.yml --storage.tsdb.path=/prometheus \
  --storage.tsdb.retention.time="${PROMETHEUS_RETENTION_TIME:-7d}" \
  --storage.tsdb.retention.size="${PROMETHEUS_RETENTION_SIZE:-2GB}" \
  --web.enable-otlp-receiver --enable-feature=exemplar-storage "$@"
