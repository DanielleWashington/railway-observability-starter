FROM grafana/loki:3.6.2
COPY config.yaml /etc/loki/config.yaml
CMD ["-config.file=/etc/loki/config.yaml", "-config.expand-env=true"]
