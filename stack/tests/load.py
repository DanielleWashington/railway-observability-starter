"""Bounded open-loop demo workload, telemetry checks, and pipeline observations."""
import concurrent.futures
import json
import os
from pathlib import Path
import statistics
import threading
import time
import uuid
import e2e

LOCK = threading.Lock()
RUN = uuid.uuid4().hex
STATS = {'sent': 0, 'ok': 0, 'errors': 0, 'skipped': 0, 'latency_ms': [], 'trace_samples': []}


def hit():
    start = time.monotonic()
    try:
        response = e2e.request(e2e.API + '/work', headers={'X-Test-Run': RUN})
        with LOCK:
            STATS['ok'] += 1
            if not STATS['trace_samples']:
                STATS['trace_samples'].append(response['trace_id'])
            elif len(STATS['trace_samples']) == 1:
                STATS['trace_samples'].append(response['trace_id'])
            else:
                STATS['trace_samples'][-1] = response['trace_id']
    except Exception:
        with LOCK: STATS['errors'] += 1
    finally:
        with LOCK: STATS['latency_ms'].append((time.monotonic()-start)*1000)


def snapshot():
    observations = {}
    queries = {
        'queue_size': 'sum by(exporter)(otelcol_exporter_queue_size)',
        'queue_capacity': 'sum by(exporter)(otelcol_exporter_queue_capacity)',
        'send_failed': 'sum({__name__=~"otelcol_exporter_send_failed_.*"})',
        'enqueue_failed': 'sum({__name__=~"otelcol_exporter_enqueue_failed_.*"})',
        'refused': 'sum({__name__=~"otelcol_receiver_refused_.*|otelcol_processor_refused_.*"})',
        'series': 'prometheus_tsdb_head_series',
    }
    for key, query in queries.items():
        observations[key] = e2e.query(query)
    return observations


def run(duration, rate, name):
    e2e.request(e2e.API + '/flush', 'POST', headers={'Authorization':'Bearer '+os.environ['DEMO_TEST_TOKEN']})
    e2e.request(e2e.DOWNSTREAM + '/flush', 'POST', headers={'Authorization':'Bearer '+os.environ['DEMO_TEST_TOKEN']})
    time.sleep(6)
    before = e2e.count()
    started = time.time()
    baseline = snapshot()
    ticks = []
    next_tick = time.monotonic()
    begin = time.monotonic()
    pending = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        for index in range(round(duration*rate)):
            due = begin + index/rate
            time.sleep(max(0,due-time.monotonic()))
            pending = {f for f in pending if not f.done()}
            if len(pending) >= 32:
                STATS['skipped'] += 1
            else:
                STATS['sent'] += 1
                pending.add(pool.submit(hit))
            if time.monotonic() >= next_tick:
                try: tick = {'at':time.time(),'pipeline':snapshot()}
                except Exception as error: tick = {'at':time.time(),'observation_error':type(error).__name__}
                ticks.append(tick)
                print(json.dumps({'event':'load_progress','phase':name,'run_id':RUN,'sent':STATS['sent'],'ok':STATS['ok'],'errors':STATS['errors'],'skipped':STATS['skipped'],'observation':tick}),flush=True)
                next_tick = time.monotonic()+60
    for endpoint in [e2e.API,e2e.DOWNSTREAM]:
        e2e.request(endpoint+'/flush','POST',headers={'Authorization':'Bearer '+os.environ['DEMO_TEST_TOKEN']})
    after = e2e.poll(lambda: e2e.count() if e2e.count() >= before+STATS['ok'] else None,120)
    end = snapshot()
    for trace in STATS['trace_samples']:
        e2e.poll(lambda: e2e.check_trace(trace),120)
        e2e.poll(lambda: e2e.check_logs(trace,RUN,start=started-60),120)
    latencies = sorted(STATS['latency_ms'])
    result = {'status':'passed','phase':name,'run_id':RUN,'started_at':started,'ended_at':time.time(),'duration_seconds':duration,'target_rps':rate,
              **{k:v for k,v in STATS.items() if k!='latency_ms'},'metric_delta':after-before,
              'latency_ms':{f'p{p}':latencies[min(len(latencies)-1,int(len(latencies)*p/100))] for p in [50,95,99]},
              'baseline':baseline,'final':end,'observations':ticks,
              'checks':['http_requests','exact_metric_delta','first_and_last_trace_log_correlation','pipeline_observations']}
    if STATS['errors'] or STATS['skipped'] or after-before!=STATS['ok'] or any('observation_error' in t for t in ticks): result['status']='failed'
    for key in ['send_failed','enqueue_failed','refused']:
        total=lambda rows: sum(float(r['value'][1]) for r in rows)
        if total(end[key])>total(baseline[key]): result['status']='failed'
    Path('/artifacts/'+name+'.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({'event':'load_complete','report':{k:v for k,v in result.items() if k!='observations'}}),flush=True)
    assert result['status']=='passed',name+' failed'


if __name__=='__main__':
    run(int(os.environ.get('LOAD_DURATION','1800')),float(os.environ.get('LOAD_RPS','10')),os.environ.get('LOAD_PHASE','load-smoke'))
