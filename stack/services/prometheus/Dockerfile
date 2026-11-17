FROM prom/prometheus:v3.8.0
COPY config.yaml /etc/prometheus/prometheus.yml
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
