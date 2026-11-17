"""A pre-existing app fixture, instrumented independently of the bundled demo."""
import base64
import json
import os
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BOOT_ID = uuid.uuid4().hex
COUNT = 0
LOCK = threading.Lock()
START = time.time_ns()


def attr(key, value):
    return {'key': key, 'value': {'stringValue': str(value)}}


def telemetry(trace_id, span_id, started, count):
    endpoint = os.environ.get('OTEL_EXPORTER_OTLP_ENDPOINT')
    if not endpoint:
        return 'disabled'
    resource = {'attributes': [attr('service.name', 'existing-app'), attr('deployment.environment.name', 'existing-project-test')]}
    now = str(time.time_ns())
    attrs = [attr('http.route', '/work')]
    signals = {
        'traces': {'resourceSpans': [{'resource': resource, 'scopeSpans': [{'scope': {'name': 'existing-fixture'}, 'spans': [{'traceId': trace_id, 'spanId': span_id, 'name': 'existing GET /work', 'kind': 2, 'startTimeUnixNano': str(started), 'endTimeUnixNano': now, 'attributes': attrs}]}]}]},
        'logs': {'resourceLogs': [{'resource': resource, 'scopeLogs': [{'scope': {'name': 'existing-fixture'}, 'logRecords': [{'timeUnixNano': now, 'observedTimeUnixNano': now, 'severityNumber': 9, 'severityText': 'INFO', 'body': {'stringValue': 'existing app request completed'}, 'traceId': trace_id, 'spanId': span_id, 'attributes': attrs}]}]}]},
        'metrics': {'resourceMetrics': [{'resource': resource, 'scopeMetrics': [{'scope': {'name': 'existing-fixture'}, 'metrics': [{'name': 'existing_app_requests', 'sum': {'aggregationTemporality': 2, 'isMonotonic': True, 'dataPoints': [{'startTimeUnixNano': str(START), 'timeUnixNano': now, 'asInt': str(count)}]}}]}]}]},
    }
    auth = 'Basic ' + base64.b64encode(('demo:' + os.environ['OTLP_PASSWORD']).encode()).decode()
    try:
        for signal, payload in signals.items():
            request = urllib.request.Request(endpoint.rstrip('/')+'/v1/'+signal, data=json.dumps(payload).encode(), headers={'Content-Type':'application/json','Authorization':auth})
            with urllib.request.urlopen(request,timeout=5) as response:
                result = json.load(response)
                if result.get('partialSuccess'): return 'partial'
        return 'exported'
    except Exception as error:
        return type(error).__name__


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        global COUNT
        body = {'status': 'ok', 'boot_id': BOOT_ID, 'application': 'existing-app'}
        if self.path == '/work':
            with LOCK:
                COUNT += 1
                count = COUNT
            trace_id, span_id = uuid.uuid4().hex, uuid.uuid4().hex[:16]
            body.update(trace_id=trace_id, span_id=span_id, count=count, telemetry=telemetry(trace_id,span_id,time.time_ns(),count))
        data = json.dumps(body).encode()
        self.send_response(200 if self.path in ['/health','/work'] else 404)
        self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(data)))
        self.end_headers(); self.wfile.write(data)


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','8080'))),Handler).serve_forever()
