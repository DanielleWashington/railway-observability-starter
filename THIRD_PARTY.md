# Third-party components

The MIT license applies to this repository's original code and configuration. It does not replace the licenses of container images, bundled software, Python packages, or their transitive dependencies.

The Dockerfiles reference these upstream projects:

- [OpenTelemetry Collector Contrib](https://github.com/open-telemetry/opentelemetry-collector-contrib)
- [Prometheus](https://github.com/prometheus/prometheus)
- [Grafana](https://github.com/grafana/grafana)
- [Tempo](https://github.com/grafana/tempo)
- [Loki](https://github.com/grafana/loki)
- [Python container images](https://github.com/docker-library/python)

See each upstream release and image for its license and notices. Python dependencies are pinned in `stack/examples/demo/requirements.txt`; the browser tests also install Playwright. No upstream source code or image layers are vendored here.
