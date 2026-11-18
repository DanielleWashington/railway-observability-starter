# Validation coverage

Status as of November 18, 2026. Results below apply to the tested runtime configuration; they are not a capacity guarantee for another workload.

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
| 24-hour Railway soak at 1 request/second | Passed: 86,400 successful requests, no errors or skipped requests; exact metric delta and first/last trace-log correlation verified |
| Deployment from this clean release repository | Passed; seven template services, full remote and browser acceptance |
| Reader guide commands, queries, and navigation | Passed through the authenticated browser Console: exact traffic and alert commands, fresh telemetry queries, and Grafana navigation |
| Deployment by an unrelated account | Deferred; not tested |

The 30-minute load test observed p95 request latency of about 15 ms and p99 of about 61 ms. The 24-hour soak observed p95 of 18.55 ms and p99 of 58.23 ms. These measurements describe the small demo workload in one environment, not general application performance.

The soak ran November 17–18, 2026. All 1,417 captured pipeline observations were collected without an observation error. There were no recorded exporter send failures, enqueue failures, or refused telemetry. Exporter queue depth peaked at one request and finished at zero. All ten validation services were healthy after the run; three are test helpers outside the seven-service template.

The local retention test accelerates cleanup and uses aged fixtures. It does not establish multi-day Railway disk headroom. The 24-hour soak is also shorter than the default log and trace retention windows.

Not validated: high availability, object-store storage, backups or recovery from volume loss, version upgrades, gRPC acceptance, legacy IPv6-only environments, or provider-specific notification integrations. Raw test records and private deployment identifiers are excluded from this repository.
