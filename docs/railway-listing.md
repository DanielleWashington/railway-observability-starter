# Deploy and Host OpenTelemetry Observability Starter on Railway

OpenTelemetry Observability Starter collects application metrics, distributed traces, and structured logs in one Grafana workspace. It combines the OpenTelemetry Collector with Prometheus, Tempo, and Loki, plus two instrumented Python services. Follow a request across services, open its logs from a trace, and receive firing and resolved alerts through an HTTPS webhook.

## About Hosting OpenTelemetry Observability Starter

This template deploys seven services and five persistent volumes. Grafana is the only public service; telemetry intake and backend queries use Railway's private network. The template generates credentials, provisions data sources and a dashboard, and configures two alert rules: demo API errors and Collector availability.

Set your HTTPS webhook URL before deployment. Once the services are healthy, open Grafana's generated domain and sign in as `admin` with `GF_SECURITY_ADMIN_PASSWORD` from its service variables. Send traffic to the private demo API from a service in the same environment, or connect your own application using an OpenTelemetry SDK and the Collector's authenticated OTLP HTTP endpoint.

## Common Use Cases

- Learn how metrics, traces, and logs describe the same request across two services.
- Add OTLP telemetry to applications in a Railway project and investigate it in Grafana.
- Try log-to-trace correlation, webhook alerts, and persistent telemetry queues before designing a larger deployment.

## Dependencies for OpenTelemetry Observability Starter Hosting

- A Railway workspace that supports at least five volumes, with resources for seven running services. Compute, storage, and network usage are billed by Railway.
- An HTTPS receiver for Grafana's generic webhook JSON. Configure it to accept the generated `ALERT_WEBHOOK_TOKEN` as a Bearer token. Provider-specific Slack, PagerDuty, and email integrations are not included.
- OpenTelemetry instrumentation in each application you want to observe. The template does not automatically collect service stdout or Railway host metrics.

### Implementation Details

The stack pins Collector Contrib 0.139.0, Prometheus 3.8.0, Tempo 2.9.0, Loki 3.6.2, and Grafana 12.3.1. Each stateful service has a 5,000 MB volume. Prometheus retains metrics for seven days or its 2 GB block-retention cap; Tempo and Loki use 72-hour retention. Retention duration is not a guarantee of disk capacity at every ingest rate.

The Collector accepts authenticated OTLP HTTP traffic on private port 4318 and persists export queues. Grafana includes request rate, error rate, latency, logs, queue, and exporter-failure panels. The demo emits trace identifiers in its logs so you can follow **View trace** in Loki and return to logs from Tempo.

This is a single-replica evaluation starter with local storage. High availability, object storage, backups, and recovery from volume loss are not included. Keep the backends private and adapt the demo dashboard and alert thresholds for your application.

## Why Deploy OpenTelemetry Observability Starter on Railway?

Railway keeps the application and its telemetry services in the same project. Reference variables connect their private addresses, generated secrets avoid shared default passwords, and volumes preserve state across service restarts. You can inspect and configure each component separately as your workload changes.
