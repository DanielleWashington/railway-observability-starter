# Validation coverage

Status as of November 17, 2026. Results below apply to the tested runtime configuration; they are not a capacity guarantee for another workload.

| Check | Result |
| --- | --- |
| Local and Railway metrics, traces, logs, dashboard, correlation | Passed |
| Clean template deployment in the maintainer's workspace | Passed |
| Add template to an existing project, including a service-name collision | Passed |
| Restart persistence and queued telemetry recovery | Passed |
| Alert firing and resolution, including an external HTTPS receiver | Passed for both provisioned rules |
| 30-minute Railway load at 10 requests/second | 18,000 successful requests, no request errors or skipped requests |
| Local accelerated retention | Expired data removed; fresh data retained in all three backends |
| Malformed and oversized OTLP HTTP requests | 57 HTTP assertions passed locally and on Railway |
| Collector credential and Grafana administrator password rotation | Passed; original credentials restored after test |
| 24-hour Railway soak at 1 request/second | In progress |
| Deployment from this clean release repository | Pending |
| Deployment by an unrelated account | Deferred; not tested |

The load test's observed p95 request latency was about 15 ms and p99 about 61 ms. This measures the small demo workload in one environment, not general application performance.

The local retention test accelerates cleanup and uses aged fixtures. It does not establish multi-day Railway disk headroom. The 24-hour soak is also shorter than the default log and trace retention windows.

Not validated: high availability, object-store storage, backups or recovery from volume loss, version upgrades, gRPC acceptance, legacy IPv6-only environments, or provider-specific notification integrations. Raw test records and private deployment identifiers are excluded from this repository.
