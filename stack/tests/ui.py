"""Browser acceptance of login, a provisioned dashboard, and panel query responses."""
import os
import json
import time
import urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

Path('/artifacts').mkdir(exist_ok=True)
Path('/artifacts/ui.json').write_text(json.dumps({'status': 'failed', 'checks': []}) + '\n')

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width':1440,'height':1000}, locale='en-US')
    failures = []
    queries = []
    populated_frames = []
    trace_results = []
    reverse_log_results = []
    capture_reverse = False
    def response(r):
        if '/api/ds/query' in r.url:
            queries.append(r)
            if r.status >= 400:
                failures.append(f'data query HTTP {r.status}')
            else:
                for result in r.json().get('results',{}).values():
                    if result.get('error'): failures.append(result['error'])
                    for frame in result.get('frames', []):
                        if any(frame.get('data', {}).get('values', [])):
                            populated_frames.append(frame)
                            if any(f.get('name') == 'traceID' for f in frame.get('schema', {}).get('fields', [])):
                                trace_results.append(frame)
                            if capture_reverse:
                                sent = r.request.post_data_json.get('queries', [])
                                if any(q.get('datasource', {}).get('uid') == 'loki'
                                       and 'demo-downstream' in q.get('expr', '')
                                       and trace_id in q.get('expr', '') for q in sent):
                                    reverse_log_results.append(frame)
    page.on('response',response)
    base=os.environ.get('GRAFANA_URL','http://grafana:3000')
    page.goto(base+'/login')
    page.get_by_placeholder('email or username').fill(os.environ['GF_SECURITY_ADMIN_USER'])
    page.get_by_placeholder('password',exact=True).fill(os.environ['GF_SECURITY_ADMIN_PASSWORD'])
    page.get_by_role('button',name='Log in',exact=True).click()
    page.wait_for_url(lambda url: '/login' not in url,timeout=30000)
    page.goto(base+'/d/observability-demo?from=now-15m&to=now')
    page.get_by_text('Request rate',exact=True).wait_for()
    page.wait_for_function("document.querySelectorAll('canvas').length > 0",timeout=30000)
    deadline = time.monotonic() + 30
    while not populated_frames and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    assert queries, 'No dashboard data requests observed'
    assert populated_frames, 'Dashboard queries returned no data'
    assert not failures, failures
    Path('/artifacts').mkdir(exist_ok=True)
    page.screenshot(path='/artifacts/dashboard.png',full_page=True)
    original = json.loads(Path('/artifacts/e2e.json').read_text())
    trace_id = original['trace_id']
    panes = {'logs': {'datasource': 'loki', 'queries': [{
        'refId': 'A', 'expr': '{service_name="demo-api"} | trace_id="' + trace_id + '"',
        'queryType': 'range', 'datasource': {'type':'loki', 'uid':'loki'},
    }], 'range': {'from':'now-1h','to':'now'}}}
    page.goto(base + '/explore?' + urllib.parse.urlencode({'schemaVersion':1,'panes':json.dumps(panes)}))
    page.get_by_text('demo request completed', exact=True).first.click(timeout=30000)
    page.screenshot(path='/artifacts/log-details.png', full_page=True)
    # Click Grafana's derived-field link, rather than constructing a Tempo URL.
    page.get_by_role('link', name='View trace', exact=True).click(timeout=15000)
    deadline = time.monotonic() + 30
    while not trace_results and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    assert trace_results, 'Log link did not load a trace'
    rendered = json.dumps(trace_results)
    assert trace_id in rendered and 'demo-api' in rendered and 'demo-downstream' in rendered
    page.get_by_text('downstream request', exact=True).first.wait_for(timeout=30000)
    assert not failures, failures
    page.screenshot(path='/artifacts/log-to-trace.png', full_page=True)
    capture_reverse = True
    page.locator('a:has(svg > title:text-is("Explore the logs for this in split view"))').last.click()
    deadline = time.monotonic() + 30
    while not reverse_log_results and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    assert reverse_log_results, 'Trace link did not query logs for the downstream service and matching trace'
    returned = json.dumps(reverse_log_results)
    assert trace_id in returned and original['run_id'] in returned and 'demo request completed' in returned
    page.get_by_text('demo request completed', exact=True).first.wait_for()
    assert not failures, failures
    page.screenshot(path='/artifacts/trace-to-log.png', full_page=True)
    report = {'status':'passed', 'checks':['login','dashboard_rendering','populated_query_results',
                                         'log_derived_field_navigation','matching_distributed_trace_rendered',
                                         'trace_to_log_navigation_and_matching_record'],
              'query_responses':len(queries), 'populated_frames':len(populated_frames)}
    Path('/artifacts/ui.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    browser.close()
