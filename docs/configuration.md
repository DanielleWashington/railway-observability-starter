# Configuration

## Send telemetry from an application

Use an OpenTelemetry SDK with the OTLP HTTP/protobuf exporter. The Collector's base endpoint is `http://<collector-private-domain>:4318`; SDK exporters append the signal path. In Railway, use reference variables for the domain and credentials.

The Collector uses HTTP Basic authentication with username `demo` and the generated `collector.OTLP_PASSWORD`. Construct the Authorization header as `Basic ` followed by Base64 of `demo:<password>`. Supply it through your SDK's header configuration without committing it. The demo supports `OTLP_USERNAME` and `OTLP_PASSWORD` directly; those names are demo-specific, not universal SDK settings.

Set a distinct `service.name` for each application. The included application dashboard and error-rate alert target `demo-api`; adapt those queries when adding your own services. Logs need trace and span identifiers to support correlation.

Backends have no application authentication and must remain private. Only expose Grafana. The Collector also configures OTLP gRPC, but the acceptance tests cover HTTP/protobuf and HTTP/JSON.

## Persistent storage and retention

| Service | Mount | Default policy |
| --- | --- | --- |
| Collector | `/var/lib/otelcol` | Persistent queues, 1,000 requests per exporter, retry window up to 10 minutes |
| Prometheus | `/prometheus` | 7 days or a 2 GB block-retention cap |
| Tempo | `/var/tempo` | 72-hour block retention |
| Loki | `/var/loki` | 72-hour retention, 2-hour deletion delay |
| Grafana | `/var/lib/grafana` | Database and user settings |

The template requests five 5,000 MB volumes. Retention settings do not guarantee that a volume can hold that duration at any ingest rate. Prometheus's block cap is not a cap on total disk use; WAL and other files also consume space. Monitor storage and adjust ingest, retention, or volume size.

Supported retention variables are `PROMETHEUS_RETENTION_TIME`, `PROMETHEUS_RETENTION_SIZE`, `TEMPO_RETENTION`, and `LOKI_RETENTION`. Loki's TSDB retention configuration requires at least 24 hours. Changes require a deployment; verify upstream limits before changing them.

Collector memory limiting is configured at 384 MiB with a 64 MiB spike allowance. This is a pipeline setting, not a Railway service memory cap. The template does not encode suggested resource caps as enforced limits.

## Alerts

Grafana evaluates the rules every 10 seconds:

- **Demo API high error rate:** more than 5% errors and more than five requests in a one-minute window, pending for 20 seconds.
- **Collector unavailable:** Prometheus cannot scrape the Collector, pending for 30 seconds.

The provisioned contact point sends Grafana webhook JSON with a Bearer token. It sends both firing and resolved notifications. Set `ALERT_WEBHOOK_URL` and configure the receiver to validate `ALERT_WEBHOOK_TOKEN`. Generic HTTPS delivery is tested; provider-specific Slack, PagerDuty, and email integrations are not configured.

## Credential changes

Changing an initialized Grafana instance's `GF_SECURITY_ADMIN_PASSWORD` environment variable does not reset its stored password. Use Grafana's supported password-change flow and retain access to the current administrator account.

Rotate `collector.OTLP_PASSWORD` together with every application's exporter credentials. Redeploy the Collector and producers, verify all three signals with the replacement credential, and confirm the retired credential returns HTTP 401. This sequence can interrupt ingestion; the starter does not implement overlapping credentials.

Do not rotate `GF_SECURITY_SECRET_KEY` casually: Grafana uses it to encrypt saved secrets. Token and encryption-key rotation are separate from the tested Collector and administrator password changes.

## Request limits

The pinned Collector rejects decoded OTLP HTTP bodies larger than 20 MiB. Loki's effective individual log-line limit is 256 KiB. A Collector HTTP 200 acknowledges intake; an asynchronous backend can still reject a record later. Monitor export failures and confirm data is queryable. Decode and size rejections can occur before pipeline counters, so also monitor producer-side HTTP errors.
