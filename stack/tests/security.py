"""Bounded OTLP HTTP rejection and credential-rotation checks against real backends."""
import gzip
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import e2e

LIMIT = 20 * 1024 * 1024  # Effective default in the pinned Collector 0.139.0.
FIELDS = {'traces': 'resourceSpans', 'metrics': 'resourceMetrics', 'logs': 'resourceLogs'}
REPORT = {'status': 'failed', 'checks': [], 'requests': [], 'started_at': time.time()}


def post(signal, body, content_type='application/json', auth=None, encoding=None):
    headers = {'Content-Type': content_type}
    if auth is not None:
        headers['Authorization'] = auth
    if encoding:
        headers['Content-Encoding'] = encoding
    request = urllib.request.Request(e2e.COLLECTOR + '/v1/' + signal, data=body, headers=headers)
    started = time.monotonic()
    try:
        response = urllib.request.urlopen(request, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.read(), (time.monotonic() - started) * 1000


def check_request(name, signal, body, expected, **kwargs):
    status, response, elapsed = post(signal, body, **kwargs)
    # Never save payloads, headers, credentials, or raw error messages.
    REPORT['requests'].append({'case': name, 'signal': signal, 'bytes': len(body),
                               'http_status': status, 'elapsed_ms': elapsed})
    assert status in expected, f'{name}/{signal}: HTTP {status}, expected {expected}'
    if name.startswith('oversized'):
        assert b'too large' in response.lower(), 'Size rejection reason missing'
    if status == 200 and response and kwargs.get('content_type', 'application/json') == 'application/json':
        data = json.loads(response)
        partial = data.get('partialSuccess', {})
        assert not any(int(partial.get(k, 0)) for k in
                       ['rejectedSpans', 'rejectedDataPoints', 'rejectedLogRecords']), 'Partial rejection'
    return status


def attr(key, value):
    return {'key': key, 'value': {'stringValue': str(value)}}


def fixture(phase, count=1, padding=0):
    trace_id = uuid.uuid4().hex
    now = time.time_ns()
    value = int(time.time())
    resource = {'attributes': [attr('service.name', 'security-probe')]}
    spans, logs, points = [], [], []
    for i in range(count):
        span_id = f'{i + 1:016x}'
        attributes = [attr('phase', phase), attr('slot', i)]
        if padding:
            attributes.append(attr('padding', 'x' * padding))
        spans.append({'traceId': trace_id, 'spanId': span_id, 'name': 'security probe',
                      'kind': 2, 'startTimeUnixNano': str(now + i),
                      'endTimeUnixNano': str(now + i + 1_000_000), 'attributes': attributes})
        logs.append({'timeUnixNano': str(now + i), 'observedTimeUnixNano': str(now + i),
                     'severityNumber': 9, 'severityText': 'INFO', 'traceId': trace_id,
                     'spanId': span_id, 'body': {'stringValue': 'security probe ' + 'x' * padding},
                     'attributes': [attr('phase', phase), attr('slot', i)]})
        points.append({'timeUnixNano': str(now + i), 'asInt': str(value), 'attributes': attributes})
    payloads = {
        'traces': {'resourceSpans': [{'resource': resource, 'scopeSpans': [{'spans': spans}]}]},
        'logs': {'resourceLogs': [{'resource': resource, 'scopeLogs': [{'logRecords': logs}]}]},
        'metrics': {'resourceMetrics': [{'resource': resource, 'scopeMetrics': [{'metrics': [
            {'name': 'security_probe_value', 'gauge': {'dataPoints': points}}]}]}]},
    }
    return {k: json.dumps(v).encode() for k, v in payloads.items()}, trace_id, value


def verify_fixture(phase, count=1, padding=0):
    payloads, trace_id, value = fixture(phase, count, padding)
    for signal, body in payloads.items():
        check_request('valid_' + phase, signal, body, [200], auth=os.environ['OTLP_AUTH'])
    def traces():
        spans = e2e.trace_spans(trace_id)
        assert len(spans) == count, 'Stored trace count differs'
        return True
    def logs():
        streams = e2e.log_query('{service_name="security-probe"} | trace_id="' + trace_id + '"')
        assert sum(len(s['values']) for s in streams) == count, 'Stored log count differs'
        return True
    def metrics():
        rows = e2e.query('security_probe_value{phase="' + phase + '"}')
        assert len(rows) == count and all(float(r['value'][1]) == value for r in rows), 'Stored metrics differ'
        return True
    for verify in [traces, logs, metrics]:
        e2e.poll(verify, 180)
    REPORT['checks'].append({'fixture': phase, 'stored_records_per_signal': count,
                             'trace_id': trace_id, 'payload_bytes': {k: len(v) for k, v in payloads.items()}})


def collector_sample():
    url = os.environ.get('COLLECTOR_METRICS_URL', 'http://collector.railway.internal:8888/metrics')
    with urllib.request.urlopen(url, timeout=10) as response:
        lines = response.read().decode().splitlines()
    names = ('otelcol_process_memory_rss', 'otelcol_process_uptime', 'otelcol_exporter_send_failed_',
             'otelcol_receiver_refused_', 'otelcol_processor_refused_', 'otelcol_exporter_enqueue_failed_')
    result = {}
    for line in lines:
        if line.startswith(names):
            metric, value, *_ = line.split()
            result[metric] = float(value)
    return result


def payloads():
    verify_fixture('before')
    samples = [{'at': time.time(), 'metrics': collector_sample()}]
    stop = threading.Event()
    def monitor():
        while not stop.wait(1):
            try:
                samples.append({'at': time.time(), 'metrics': collector_sample()})
            except Exception as error:
                samples.append({'at': time.time(), 'error': type(error).__name__})
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    try:
        for signal, field in FIELDS.items():
            empty = json.dumps({field: []}).encode()
            cases = [
                ('malformed_json', b'{"' + field.encode() + b'":', 'application/json', None, [400]),
                ('wrong_json_type', json.dumps({field: 'not-an-array'}).encode(), 'application/json', None, [400]),
                ('malformed_protobuf', b'\x80', 'application/x-protobuf', None, [400]),
                ('unsupported_content_type', empty, 'text/plain', None, [415]),
                ('invalid_gzip', b'not-a-gzip-stream', 'application/json', 'gzip', [400]),
            ]
            for name, body, kind, encoding, expected in cases:
                check_request(name, signal, body, expected, content_type=kind,
                              encoding=encoding, auth=os.environ['OTLP_AUTH'])
                check_request('valid_after_' + name, signal, empty, [200], auth=os.environ['OTLP_AUTH'])
            # Valid JSON with trailing whitespace: rejection is due to size, not JSON syntax.
            oversized = empty + b' ' * (LIMIT + 1 - len(empty))
            for encoding in [None, 'gzip']:
                body = gzip.compress(oversized) if encoding else oversized
                check_request('oversized_' + (encoding or 'plain'), signal, body, [400, 413],
                              encoding=encoding, auth=os.environ['OTLP_AUTH'])
                check_request('valid_after_oversized', signal, empty, [200], auth=os.environ['OTLP_AUTH'])
            check_request('auth_before_decode', signal, b'{', [401])
            check_request('valid_empty_protobuf', signal, b'', [200],
                          content_type='application/x-protobuf', auth=os.environ['OTLP_AUTH'])
        verify_fixture('large', count=256, padding=4096)
        verify_fixture('after')
        # Leave time for exporter failure counters and memory pressure to settle.
        time.sleep(15)
    finally:
        stop.set()
        thread.join(timeout=12)
        samples.append({'at': time.time(), 'metrics': collector_sample()})
        REPORT['collector_observations'] = samples
    assert not any('error' in s for s in samples), 'Collector metrics became unavailable'
    first, last = samples[0]['metrics'], samples[-1]['metrics']
    uptime = [key for key in first if key.startswith('otelcol_process_uptime')]
    assert uptime and all(last[key] >= first[key] for key in uptime), 'Collector restarted'
    rss = [value for sample in samples for key, value in sample['metrics'].items()
           if key.startswith('otelcol_process_memory_rss')]
    assert rss and max(rss) < 600_000_000, 'Collector exceeded staging memory limit'
    failures = ('otelcol_exporter_send_failed_', 'otelcol_receiver_refused_',
                'otelcol_processor_refused_', 'otelcol_exporter_enqueue_failed_')
    assert all(value <= first.get(key, 0) for key, value in last.items()
               if key.startswith(failures)), 'Pipeline failures increased during bounded rejection tests'
    REPORT['max_observed_collector_rss_bytes'] = max(rss)
    REPORT['effective_body_limit_bytes'] = LIMIT
    REPORT['checks'].append('collector_survived_rejections_without_pipeline_failure_increase')


def rotation():
    for signal, field in FIELDS.items():
        body = json.dumps({field: []}).encode()
        check_request('retired_credential', signal, body, [401], auth=os.environ['RETIRED_OTLP_AUTH'])
        check_request('active_credential', signal, body, [200], auth=os.environ['OTLP_AUTH'])
        check_request('missing_credential', signal, body, [401])
    verify_fixture('rotated' if os.environ['SECURITY_MODE'] == 'rotation' else 'restored')


def backend_limits():
    def failed_logs():
        return sum(value for key, value in collector_sample().items()
                   if key.startswith('otelcol_exporter_send_failed_log_records'))
    before = failed_logs()
    payloads, trace_id, _ = fixture('oversized-record', padding=512 * 1024)
    body = payloads['logs']
    assert len(body) < LIMIT
    check_request('accepted_by_collector_before_backend_rejection', 'logs', body, [200],
                  auth=os.environ['OTLP_AUTH'])
    e2e.poll(lambda: failed_logs() > before, 120)
    assert not e2e.log_query('{service_name="security-probe"} | trace_id="' + trace_id + '"'), \
        'Oversized log unexpectedly stored'
    verify_fixture('after-backend-reject')
    def queue_drained():
        rows = e2e.query('sum(otelcol_exporter_queue_size{exporter="otlphttp/logs"})')
        return bool(rows) and float(rows[0]['value'][1]) == 0
    e2e.poll(queue_drained, 120)
    REPORT['checks'].append({'backend': 'loki', 'oversized_body_bytes': 512 * 1024 + len('security probe '),
                             'request_bytes': len(body), 'collector_http_status': 200,
                             'stored': False, 'failed_log_record_delta': failed_logs() - before,
                             'queue_drained': True})


if __name__ == '__main__':
    mode = os.environ['SECURITY_MODE']
    REPORT['mode'] = mode
    try:
        if mode == 'payloads':
            payloads()
        elif mode == 'backend-limits':
            backend_limits()
        elif mode in ('rotation', 'restored'):
            rotation()
        elif mode in ('producer-rotated', 'producer-restored'):
            REPORT['trace_id'] = e2e.run()
            REPORT['checks'] = e2e.RESULTS
        else:
            raise ValueError('Unknown security mode')
        REPORT['status'] = 'passed'
    except Exception as error:
        REPORT['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        REPORT['ended_at'] = time.time()
        Path('/artifacts/security-' + mode + '.json').write_text(json.dumps(REPORT, indent=2))
