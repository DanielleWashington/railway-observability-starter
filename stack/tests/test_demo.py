"""SDK integration against a recording OTLP receiver, not backend acceptance."""
import base64
import gzip
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest

ROOT = Path(__file__).resolve().parents[1]
traces, metrics, logs, received_auth = [], [], [], []
lock = threading.Lock()

class Intake(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        if self.headers.get('Content-Encoding') == 'gzip': body = gzip.decompress(body)
        message_type, destination = {
            '/v1/traces': (ExportTraceServiceRequest, traces),
            '/v1/metrics': (ExportMetricsServiceRequest, metrics),
            '/v1/logs': (ExportLogsServiceRequest, logs),
        }[self.path]
        message = message_type()
        message.ParseFromString(body)
        with lock:
            destination.append(message)
            received_auth.append((self.path, self.headers.get('Authorization')))
        self.send_response(200)
        self.send_header('Content-Type','application/x-protobuf')
        self.send_header('Content-Length','0')
        self.end_headers()

def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0))
        return s.getsockname()[1]

def call(base, path, method='GET', headers=None):
    try:
        r = urllib.request.urlopen(urllib.request.Request(base+path, method=method, headers=headers or {}), timeout=5)
    except urllib.error.HTTPError as e: r=e
    with r: return r.status, json.load(r)

class DemoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.receiver = ThreadingHTTPServer(('127.0.0.1',0), Intake)
        threading.Thread(target=cls.receiver.serve_forever,daemon=True).start()
        cls.processes=[]
        cls.logs=[]
        down_port, api_port = port(), port()
        cls.down=f'http://127.0.0.1:{down_port}'
        cls.api=f'http://127.0.0.1:{api_port}'
        for name,p in [('demo-downstream',down_port),('demo-api',api_port)]:
            env={**os.environ,'PORT':str(p),'OTEL_SERVICE_NAME':name,
                 'OTEL_EXPORTER_OTLP_ENDPOINT':f'http://127.0.0.1:{cls.receiver.server_port}',
                 'OTLP_USERNAME':'demo', 'OTLP_PASSWORD':'sdk-generated-credential-test',
                 'DEMO_TEST_TOKEN':'test-only'}
            if name=='demo-api': env['DOWNSTREAM_URL']=cls.down
            cls.processes.append(subprocess.Popen([sys.executable,str(ROOT/'examples/demo/app.py')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE))
        end=time.monotonic()+20
        for base in [cls.down,cls.api]:
            while True:
                try:
                    if call(base,'/health')[0]==200: break
                except (OSError,ValueError): pass
                if time.monotonic()>end:
                    cls.tearDownClass()
                    raise AssertionError('Demo failed to start')
                time.sleep(.1)

    @classmethod
    def tearDownClass(cls):
        for p in cls.processes: p.terminate()
        for p in cls.processes:
            try: p.communicate(timeout=10)
            except subprocess.TimeoutExpired: p.kill(); p.communicate()
        cls.receiver.shutdown()
        cls.receiver.server_close()

    def flush(self):
        for base in [self.down,self.api]:
            self.assertEqual(call(base,'/flush','POST',{'Authorization':'Bearer test-only'})[0],200)

    def test_01_propagation(self):
        code,data=call(self.api,'/work',headers={'Traceparent':'00-1234567890abcdef1234567890abcdef-1234567890abcdef-01'})
        self.assertEqual(code,200)
        self.assertEqual(data['trace_id'],'1234567890abcdef1234567890abcdef')
        self.assertEqual(data['trace_id'],data['downstream']['trace_id'])
        self.flush()
        spans=[s for r in traces for b in r.resource_spans for scope in b.scope_spans for s in scope.spans if s.trace_id.hex()==data['trace_id']]
        self.assertEqual(len(spans),3)
        client=next(s for s in spans if s.kind==3)
        self.assertTrue(any(s.span_id==client.parent_span_id for s in spans))
        self.assertTrue(any(s.parent_span_id==client.span_id for s in spans))

    def test_02_error_and_slow(self):
        start=time.monotonic()
        self.assertEqual(call(self.api,'/slow')[0],200)
        self.assertGreaterEqual(time.monotonic()-start,.25)
        code,data=call(self.api,'/error')
        self.assertEqual(code,500)
        self.flush()
        span=next(s for r in traces for b in r.resource_spans for scope in b.scope_spans for s in scope.spans if s.trace_id.hex()==data['trace_id'])
        self.assertEqual(span.status.code,2)
        self.assertTrue(any(e.name=='exception' for e in span.events))

    def test_03_counter(self):
        def value():
            for r in reversed(metrics):
                for b in r.resource_metrics:
                    if not any(a.key=='service.name' and a.value.string_value=='demo-api' for a in b.resource.attributes): continue
                    for scope in b.scope_metrics:
                        for m in scope.metrics:
                            if m.name=='demo_requests': return sum(p.as_int for p in m.sum.data_points)
            return 0
        self.flush()
        before=value()
        for _ in range(100): self.assertEqual(call(self.api,'/work')[0],200)
        self.flush()
        self.assertEqual(value()-before,100)
        histograms=[m for r in metrics for b in r.resource_metrics for scope in b.scope_metrics
                    for m in scope.metrics if m.name=='demo_request_duration']
        self.assertTrue(histograms)
        # Latencies are recorded in seconds; millisecond-scale buckets are needed
        # to avoid reporting a multi-second p95 for sub-second requests.
        self.assertIn(0.005, histograms[-1].histogram.data_points[0].explicit_bounds)

    def test_04_protected_flush(self):
        self.assertEqual(call(self.api,'/flush','POST')[0],403)
        self.assertEqual(call(self.api,'/flush','POST',{'Authorization':'Bearer wrong'})[0],403)
        self.assertEqual(call(self.api,'/unknown')[0],404)

    def test_05_correlated_logs(self):
        for route, code, severity in [('/work', 200, 9), ('/error', 500, 17)]:
            _, data = call(self.api, route, headers={'X-Test-Run': 'sdk-log-check'})
            self.flush()
            records = [(b, r) for export in logs for b in export.resource_logs
                       for scope in b.scope_logs for r in scope.log_records
                       if r.trace_id.hex() == data['trace_id']]
            self.assertEqual(len(records), 2 if code == 200 else 1)
            spans = [s for export in traces for b in export.resource_spans
                     for scope in b.scope_spans for s in scope.spans
                     if s.trace_id.hex() == data['trace_id']]
            for resource, record in records:
                self.assertEqual(record.body.string_value, 'demo request completed')
                self.assertEqual(record.severity_number, severity)
                self.assertTrue(any(s.span_id == record.span_id for s in spans))
                attrs = {a.key: a.value for a in record.attributes}
                self.assertEqual(attrs['test.run_id'].string_value, 'sdk-log-check')
                self.assertEqual(attrs['http.response.status_code'].int_value, code)

    def test_06_template_credentials(self):
        call(self.api, '/work')
        self.flush()
        expected = 'Basic ' + base64.b64encode(b'demo:sdk-generated-credential-test').decode()
        self.assertEqual({path for path, _ in received_auth}, {'/v1/traces','/v1/metrics','/v1/logs'})
        self.assertTrue(all(value == expected for _, value in received_auth))

if __name__=='__main__': unittest.main()
