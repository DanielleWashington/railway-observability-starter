# Running additional tests

Run the standard checks in the repository README first. Keep tests separate from production traffic: they trigger alerts, stop backends, and exercise rejection paths.

## Load

The load runner sends a bounded workload and verifies telemetry and Collector pipeline counters. To run a 30-minute, 10-request-per-second test against the local Compose stack, from `stack/`:

```sh
docker compose -f compose.yaml -f tests/load-compose.yaml run --build --rm --no-deps \
  -e LOAD_DURATION=1800 -e LOAD_RPS=10 -e LOAD_PHASE=load-smoke \
  --entrypoint python tests /tests/load.py
```

Reports are written under `stack/artifacts/`. Duration and rate are explicit; a 24-hour soak uses `LOAD_DURATION=86400` and `LOAD_RPS=1`. Running these commands requires a healthy stack and leaves its data intact.

## Retention

```sh
python3 scripts/test-retention.py
```

This creates a separate Compose project with real Prometheus, Tempo, and Loki engines. It uses aged metrics and logs with the configured retention durations, a 60-second Tempo test window, and accelerated cleanup cycles. It verifies both expired-data removal and fresh-data survival. It is not a multi-day Railway capacity test. Allow up to roughly 40 minutes for Loki's query cache and deletion behavior; laptop sleep can extend wall-clock time.

Reports name the isolated Compose project. Stop the reported project with `docker compose -p <reported-project> -f tests/retention/compose.yaml down`. This preserves its volumes. Add `--volumes` only when you are ready to delete that test data.

## Railway validation

The repository includes reusable test runners, but it does not include the maintainer's project-specific deployment automation or credentials. A remote test service must run inside the same Railway environment, use private service addresses, and receive the relevant credentials through service variables. Do not publish test endpoints or put credentials in command arguments.

The external HTTPS receiver in `tests/https_alert_sink.py` is a bounded test fixture with separate write and read tokens. It is not a durable alerting system. Remove it after validation.
