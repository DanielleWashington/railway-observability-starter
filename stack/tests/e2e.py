"""Real OTLP -> backend -> authenticated Grafana queries; no mocks."""
import base64
import json
import os
import sys
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

API = os.environ.get('API_URL', 'http://localhost:8080')
GRAFANA = os.environ.get('GRAFANA_URL', 'http://localhost:3000')
DOWNSTREAM = os.environ.get('DOWNSTREAM_URL', 'http://downstream:8080')
COLLECTOR = os.environ.get('COLLECTOR_URL', 'http://collector:4318')
SINK = os.environ.get('ALERT_SINK_URL', 'http://alert-sink:8080')
AUTH = 'Basic ' + base64.b64encode(('admin:' + os.environ['GF_SECURITY_ADMIN_PASSWORD']).encode()).decode()
RUN = uuid.uuid4().hex
RESULTS = []

def request(url, method='GET', body=None, headers=None, expected=200):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        data = response.read()
        assert response.status == expected, f'{method} {urllib.parse.urlsplit(url).path}: HTTP {response.status}, expected {expected}'
        return json.loads(data) if data and response.headers.get_content_type() == 'application/json' else data

def poll(fn, timeout=120):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            value = fn()
            if value:
                return value
        except (AssertionError, urllib.error.URLError, TimeoutError, KeyError, ValueError) as error:
            last = type(error).__name__ + ': ' + str(error)
        time.sleep(1)
    raise AssertionError('Timed out: ' + str(last))

def grafana(path):
    return request(GRAFANA + path, headers={'Authorization': AUTH, 'Accept':'application/json'})

def query(expr):
    data = grafana('/api/datasources/proxy/uid/prometheus/api/v1/query?' + urllib.parse.urlencode({'query':expr}))
    assert data['status'] == 'success'
    return data['data']['result']

def count(metric='demo_requests_total'):
    result = query('sum(' + metric + '{service="demo-api",route="/work",status="200"})')
    return float(result[0]['value'][1]) if result else 0

def log_query(expr, start=None, end=None):
    options = {'query': expr, 'limit': 1000}
    if start is None:
        options['since'] = '1h'
    else:
        options['start'] = str(int(start * 1e9))
        options['end'] = str(int((end or time.time()) * 1e9))
    params = urllib.parse.urlencode(options)
    data = grafana('/api/datasources/proxy/uid/loki/loki/api/v1/query_range?' + params)
    assert data['status'] == 'success'
    return data['data']['result']

def check_logs(trace_id, run_id, expected_error=False, start=None, end=None):
    streams = log_query('{service_name=~"demo-api|demo-downstream"} | trace_id="' +
                        trace_id + '" | test_run_id="' + run_id + '"', start=start, end=end)
    expected = {'demo-api'} if expected_error else {'demo-api', 'demo-downstream'}
    assert expected == {s['stream']['service_name'] for s in streams}, 'Correlated logs missing'
    spans = trace_spans(trace_id)
    for stream in streams:
        for value in stream['values']:
            assert value[1] == 'demo request completed'
            # Loki's default query encoding merges structured metadata into the
            # returned stream labels (the /series API below checks real indexes).
            metadata = stream['stream']
            assert metadata['trace_id'] == trace_id
            matching = [s for a,s in spans if a['service.name'] == stream['stream']['service_name']]
            # Tempo JSON may represent span IDs as hex or protobuf base64.
            ids = {s['spanId'] for s in matching}
            assert metadata['span_id'] in ids or base64.b64encode(bytes.fromhex(metadata['span_id'])).decode() in ids
            if expected_error:
                assert metadata['severity_text'] == 'ERROR'
    return True

def notifications():
    token = (os.environ.get('ALERT_RECEIVER_READ_TOKEN') or
             os.environ.get('ALERT_WEBHOOK_TOKEN', os.environ['DEMO_TEST_TOKEN']))
    return request(SINK + '/events', headers={'Authorization': 'Bearer ' + token})

def notification(title, status, since, firing=None):
    for event in notifications():
        if event['received_at'] < since:
            continue
        for alert in event['payload']['alerts']:
            if alert['labels'].get('alertname') != title or alert['status'] != status:
                continue
            if firing and (alert['fingerprint'], alert['startsAt']) != (firing['fingerprint'], firing['startsAt']):
                continue
            expected_service = 'demo-api' if title == 'Demo API high error rate' else 'collector'
            expected_uid = 'demo-high-error-rate' if expected_service == 'demo-api' else 'collector-unavailable'
            assert alert['labels']['service'] == expected_service
            assert alert['labels']['severity'] == ('warning' if expected_service == 'demo-api' else 'critical')
            assert urllib.parse.urlsplit(alert['generatorURL']).path == '/alerting/grafana/' + expected_uid + '/view'
            if expected_service == 'demo-api':
                assert '/d/observability-demo' in alert['dashboardURL']
            if status == 'resolved':
                assert alert['endsAt'] > alert['startsAt']
            return alert
    return None

def healthy_rule(title):
    groups = grafana('/api/prometheus/grafana/api/v1/rules')['data']['groups']
    rule = next(r for g in groups for r in g['rules'] if r['name'] == title)
    assert rule['health'] == 'ok', rule.get('lastError', 'Unhealthy alert rule')
    return rule['state'] == 'inactive'

def alerts(mode):
    if mode == '--alerts':
        title = 'Demo API high error rate'
        request(SINK + '/events', expected=403)
        request(SINK + '/webhook', 'POST', b'{}', expected=403)
        poll(lambda: healthy_rule(title), 180)
        since = time.time()
        def trigger():
            request(API + '/error', expected=500)
            request(API + '/error', expected=500)
            request(API + '/work')
            return notification(title, 'firing', since)
        firing = poll(trigger, 180)
        RESULTS.append('error_rate_firing_webhook')
        def resolve():
            request(API + '/work')
            return notification(title, 'resolved', since, firing)
        poll(resolve, 180)
        poll(lambda: healthy_rule(title))
        RESULTS.append('error_rate_resolved_webhook_same_incident')
    elif mode == '--collector-firing':
        # A remote runner may deploy after the first outage notification arrives.
        since = float(os.environ.get('COLLECTOR_OUTAGE_STARTED_AT') or time.time())
        firing = poll(lambda: notification('Collector unavailable', 'firing', since), 180)
        Path('/artifacts/collector-incident.json').write_text(json.dumps({'since': since, 'firing': firing}))
        RESULTS.append('collector_unavailable_firing_webhook')
    elif mode == '--collector-resolved':
        incident = json.loads(Path('/artifacts/collector-incident.json').read_text())
        poll(lambda: notification('Collector unavailable', 'resolved', incident['since'], incident['firing']), 180)
        poll(lambda: healthy_rule('Collector unavailable'))
        RESULTS.append('collector_unavailable_resolved_webhook_same_incident')
    return None

def flush():
    for url in [DOWNSTREAM, API]:
        request(url + '/flush', 'POST', b'', {'Authorization':'Bearer ' + os.environ['DEMO_TEST_TOKEN']})

def trace_spans(trace_id):
    data = grafana('/api/datasources/proxy/uid/tempo/api/traces/' + trace_id)
    spans = []
    for batch in data.get('batches', data.get('resourceSpans', [])):
        attrs = {a['key']: a['value'].get('stringValue') for a in batch['resource']['attributes']}
        for scope in batch.get('scopeSpans', batch.get('instrumentationLibrarySpans', [])):
            spans.extend((attrs, s) for s in scope['spans'])
    return spans

def check_trace(trace_id, expected_error=False, expected_slow=False):
    spans = trace_spans(trace_id)
    api = [s for a,s in spans if a.get('service.name') == 'demo-api' and str(s['kind']) in ('2','SPAN_KIND_SERVER')]
    assert api, 'API server span missing'
    server = api[0]
    if expected_error:
        assert str(server.get('status', {}).get('code')) in ('2', 'STATUS_CODE_ERROR')
        assert any(e['name'] == 'exception' for e in server.get('events', []))
    else:
        client = [s for a,s in spans if s['name'] == 'downstream request']
        downstream = [s for a,s in spans if a.get('service.name') == 'demo-downstream']
        assert client and downstream, 'Distributed spans missing'
        assert client[0]['parentSpanId'] == server['spanId']
        assert downstream[0]['parentSpanId'] == client[0]['spanId']
    if expected_slow:
        assert int(server['endTimeUnixNano']) - int(server['startTimeUnixNano']) >= 250_000_000
    return True

def run():
    poll(lambda: request(API + '/health'), 600)
    poll(lambda: request(DOWNSTREAM + '/health'), 600)
    poll(lambda: grafana('/api/health').get('database') == 'ok', 600)
    poll(lambda: grafana('/api/datasources/proxy/uid/prometheus/-/ready'))
    poll(lambda: query('up{job="prometheus"}'))
    RESULTS.append('readiness')
    request(GRAFANA + '/api/datasources', expected=401)
    for auth,code in [(None,401), ('Basic aW52YWxpZDppbnZhbGlk',401), (os.environ['OTLP_AUTH'],200)]:
        headers = {'Content-Type':'application/json'}
        if auth:
            headers['Authorization'] = auth
        for signal, field in [('traces','resourceSpans'), ('logs','resourceLogs'), ('metrics','resourceMetrics')]:
            request(COLLECTOR + '/v1/' + signal, 'POST', json.dumps({field: []}).encode(), headers, code)
    request(API + '/flush','POST',b'',expected=403)
    RESULTS.append('authentication')
    baseline_started = time.time()
    sample = request(API + '/work', headers={'X-Test-Run':RUN})
    assert sample['trace_id'] == sample['downstream']['trace_id']
    flush()
    # A prior run's samples remain queryable. Wait for this flush before recording
    # the baseline; otherwise the warm-up request can be counted among the 100.
    for metric in ['demo_requests_total', 'demo_request_duration_seconds_count']:
        def fresh():
            result = query('min(timestamp(' + metric + '{service="demo-api",route="/work",status="200"}))')
            return bool(result) and float(result[0]['value'][1]) >= baseline_started
        poll(fresh)
    before = count()
    before_hist = count('demo_request_duration_seconds_count')
    for _ in range(100):
        request(API + '/work', headers={'X-Test-Run':RUN})
    flush()
    poll(lambda: count() == before + 100)
    poll(lambda: count('demo_request_duration_seconds_count') == before_hist + 100)
    RESULTS.append('100_request_counter_and_histogram')
    poll(lambda: check_trace(sample['trace_id']))
    slow = request(API + '/slow')
    error = request(API + '/error', headers={'X-Test-Run':RUN}, expected=500)
    flush()
    poll(lambda: check_trace(slow['trace_id'], expected_slow=True))
    poll(lambda: check_trace(error['trace_id'], expected_error=True))
    RESULTS.append('distributed_error_and_slow_traces')
    poll(lambda: check_logs(sample['trace_id'], RUN))
    poll(lambda: check_logs(error['trace_id'], RUN, expected_error=True))
    # Loki's stream API exposes index labels; query results can also include
    # structured metadata, so they cannot prove the index cardinality boundary.
    params = urllib.parse.urlencode({'match[]': '{service_name=~"demo-api|demo-downstream"}'})
    indexed = grafana('/api/datasources/proxy/uid/loki/loki/api/v1/series?' + params)['data']
    assert indexed and all(not {'trace_id', 'span_id', 'test_run_id'} & set(s) for s in indexed)
    loki = grafana('/api/datasources/uid/loki')['jsonData']['derivedFields'][0]
    assert loki['datasourceUid'] == 'tempo' and loki['url'] == '${__value.raw}'
    tempo = grafana('/api/datasources/uid/tempo')['jsonData']['tracesToLogsV2']
    assert tempo['datasourceUid'] == 'loki' and '${__trace.traceId}' in tempo['query']
    RESULTS.append('otlp_logs_trace_span_correlation_and_bounded_index_labels')
    series = query('demo_requests_total')
    assert {'demo-api','demo-downstream'} <= {s['metric']['service'] for s in series}
    assert all('test_run_id' not in s['metric'] for s in series)
    dashboard = grafana('/api/dashboards/uid/observability-demo')['dashboard']
    assert len(dashboard['panels']) >= 4
    for panel in dashboard['panels']:
        for target in panel['targets']:
            (log_query if panel['datasource']['uid'] == 'loki' else query)(target['expr'])
    RESULTS.append('grafana_dashboard_queries_and_labels')
    # This dashboard exists only in Grafana's database, not provisioning files.
    request(GRAFANA + '/api/dashboards/db', 'POST', json.dumps({
        'dashboard': {'uid': 'persistence-check', 'title': 'Persistence check',
                      'tags': [RUN], 'schemaVersion': 39, 'panels': []},
        'overwrite': True,
    }).encode(), {'Authorization': AUTH, 'Content-Type': 'application/json'})
    RESULTS.append('saved_dashboard_created')
    return sample['trace_id']

def recovery(mode):
    path = Path('/artifacts/recovery.json')
    if mode == '--enqueue':
        sample = request(API + '/work', headers={'X-Test-Run': RUN})
        flush()
        for exporter in ['traces', 'logs']:
            def queued():
                result = query('sum(otelcol_exporter_queue_size{exporter="otlphttp/' + exporter + '"})')
                return bool(result) and float(result[0]['value'][1]) > 0
            poll(queued)
        path.write_text(json.dumps({'trace_id': sample['trace_id'], 'run_id': RUN}))
        RESULTS.append('trace_and_logs_confirmed_in_persistent_queues')
        return sample['trace_id']
    if mode == '--verify-queue':
        original = json.loads(path.read_text())
    else:
        original = json.loads(Path('/artifacts/e2e.json').read_text())
    trace_id = original['trace_id']
    poll(lambda: check_trace(trace_id), 180)
    poll(lambda: check_logs(trace_id, original['run_id']), 180)
    poll(lambda: count() >= 100)
    if mode == '--verify-queue':
        def drained():
            value = query('sum(otelcol_exporter_queue_size{exporter=~"otlphttp/(traces|logs)"})')
            return bool(value) and float(value[0]['value'][1]) == 0
        poll(drained)
        poll(lambda: healthy_rule('Collector unavailable'))
        RESULTS.append('trace_and_log_queues_drained')
    grafana('/api/dashboards/uid/observability-demo')
    if mode == '--verify-restart':
        original = json.loads(Path('/artifacts/e2e.json').read_text())
        saved = grafana('/api/dashboards/uid/persistence-check')['dashboard']
        assert original['run_id'] in saved['tags'], 'Saved Grafana state did not survive restart'
        RESULTS.append('historical_metrics_traces_logs_and_saved_dashboard_survived')
    RESULTS.append(mode)
    return trace_id

if __name__ == '__main__':
    report = {'run_id':RUN, 'passed':RESULTS, 'status':'failed'}
    try:
        mode = sys.argv[1] if len(sys.argv) > 1 else None
        report['trace_id'] = (alerts(mode) if mode in ('--alerts', '--collector-firing', '--collector-resolved')
                              else recovery(mode) if mode else run())
        report['status'] = 'passed'
    finally:
        output = Path(os.environ.get('REPORT_PATH','/artifacts/e2e.json'))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))
