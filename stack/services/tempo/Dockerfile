FROM grafana/tempo:2.9.0
COPY config.yaml /etc/tempo.yaml
CMD ["-config.file=/etc/tempo.yaml", "-config.expand-env=true"]
