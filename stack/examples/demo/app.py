"""Two roles share instrumentation, with explicit spans and bounded metric labels."""
import base64
import hmac
import json
import logging
import os
import signal
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.view import View, ExplicitBucketHistogramAggregation
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode

# Template-generated credentials are referenced separately, because Railway's
# secret() function does not encode Basic-auth headers. Never log these values.
if "OTLP_PASSWORD" in os.environ:
    username = os.environ.get("OTLP_USERNAME", "demo")
    password = os.environ["OTLP_PASSWORD"]
    if not username or ":" in username or not password:
        raise ValueError("OTLP username and password must be configured")
    token = base64.b64encode((username + ":" + password).encode()).decode()
    os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = "Authorization=Basic " + token

SERVICE = os.environ.get("OTEL_SERVICE_NAME", "demo-api")
resource = Resource.create({"service.name": SERVICE,
    "deployment.environment.name": os.environ.get("DEPLOYMENT_ENVIRONMENT", "local")})
provider = TracerProvider(resource=resource)
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)
logs = LoggerProvider(resource=resource)
logs.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
logger = logging.getLogger("demo.requests")
logger.setLevel(logging.INFO)
logger.propagate = False
logger.addHandler(LoggingHandler(logger_provider=logs))
reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=5000)
metrics = MeterProvider(resource=resource, metric_readers=[reader], views=[
    View(instrument_name="demo_request_duration", aggregation=ExplicitBucketHistogramAggregation(
        boundaries=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]))
])
meter = metrics.get_meter(__name__)
requests = meter.create_counter("demo_requests")
duration = meter.create_histogram("demo_request_duration", unit="s")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        token = os.environ.get("DEMO_TEST_TOKEN", "")
        if self.path != "/flush" or not token or not hmac.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + token):
            self.reply(403, {"error": "forbidden"})
            return
        ok = all([provider.force_flush(), metrics.force_flush(), logs.force_flush()])
        self.reply(200 if ok else 503, {"flushed": ok})

    def do_GET(self):
        route = urlsplit(self.path).path
        if route == "/health":
            self.reply(200, {"status": "ok"})
            return
        if route not in {"/work", "/slow", "/error"}:
            self.reply(404, {"error": "not found"})
            return
        start = time.monotonic()
        status = 200
        with tracer.start_as_current_span("GET " + route,
                context=propagate.extract({k.lower(): v for k, v in self.headers.items()}), kind=SpanKind.SERVER) as span:
            span.set_attribute("http.route", route)
            span.set_attribute("http.request.method", "GET")
            span.set_attribute("test.run_id", self.headers.get("X-Test-Run", "")[:128])
            result = {"service": SERVICE, "trace_id": format(span.get_span_context().trace_id, "032x")}
            try:
                if route == "/slow":
                    time.sleep(0.25)
                if route == "/error":
                    raise ValueError("controlled demo failure")
                downstream = os.environ.get("DOWNSTREAM_URL")
                if downstream:
                    with tracer.start_as_current_span("downstream request", kind=SpanKind.CLIENT):
                        headers = {"X-Test-Run": self.headers.get("X-Test-Run", "")[:128]}
                        propagate.inject(headers)
                        with urllib.request.urlopen(urllib.request.Request(downstream + "/work", headers=headers), timeout=5) as response:
                            result["downstream"] = json.load(response)
            except Exception as error:
                status = 500
                span.record_exception(error)
                span.set_status(Status(StatusCode.ERROR, str(error)))
                result["error"] = "request failed"
            span.set_attribute("http.response.status_code", status)
            labels = {"service": SERVICE, "route": route, "status": str(status)}
            requests.add(1, labels)
            duration.record(time.monotonic() - start, labels)
            # The SDK attaches the active span and trace IDs. Run IDs remain log
            # metadata, never metric or Loki index labels.
            logger.log(logging.ERROR if status == 500 else logging.INFO,
                "demo request completed", extra={
                    "http.route": route, "http.response.status_code": status,
                    "test.run_id": self.headers.get("X-Test-Run", "")[:128],
                })
            print(json.dumps({**result, "route": route, "status": status}), flush=True)
        self.reply(status, result)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler)
    server.daemon_threads = False
    def shutdown(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        provider.shutdown()
        metrics.shutdown()
        logs.shutdown()
