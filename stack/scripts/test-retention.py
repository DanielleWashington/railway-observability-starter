"""Isolated real-engine expiry tests with aged data and faster cleanup cycles.

Prometheus keeps 7d and Loki 72h. Tempo uses a 60s real-time test window.
Flush, compaction, and deletion timings are accelerated only in the test profile.
"""
import json,os,subprocess,time,urllib.request,urllib.error,urllib.parse,uuid,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ART=ROOT/'artifacts/retention'; ART.mkdir(parents=True,exist_ok=True)
PROJECT=os.environ.get('RETENTION_PROJECT')
if not PROJECT and '--resume-loki' in sys.argv:
 PROJECT=json.loads((ART/'retention.json').read_text()).get('compose_project','obs-retention-v2')
PROJECT=PROJECT or 'obs-retention-'+uuid.uuid4().hex[:8]
assert PROJECT.startswith('obs-retention-'), 'Use an isolated retention project name'
COMPOSE=['docker','compose','-p',PROJECT,'-f',str(ROOT/'tests/retention/compose.yaml')]
VOLUME_PREFIX=PROJECT+'_'
REPORT={'compose_project':PROJECT,'status':'running','started_at':time.time(),'method':'aged metrics/logs with production retention durations; Tempo real-time block aging with 60s test retention; accelerated cleanup cycles','checks':{}}

def command(*args,env=None):
 r=subprocess.run(args,cwd=ROOT,env=env,text=True,capture_output=True,check=True);return r.stdout

def req(url,payload=None,method=None):
 data=json.dumps(payload).encode() if payload is not None else None
 q=urllib.request.Request(url,data=data,headers={'Content-Type':'application/json','Accept':'application/json'},method=method)
 with urllib.request.urlopen(q,timeout=15) as r:
  data=r.read();return json.loads(data) if data and data[:1] in (b'{',b'[') else data.decode()

def wait(fn,seconds=600):
 end=time.monotonic()+seconds;last=None
 while time.monotonic()<end:
  try:
   result=fn()
   if result:return result
  except Exception as e:last=type(e).__name__+': '+str(e)
  time.sleep(3)
 raise AssertionError('Timed out: '+str(last))

def save():
 (ART/'retention.json').write_text(json.dumps(REPORT,indent=2)+'\n')
 print(json.dumps({'status':REPORT['status'],'checks':REPORT['checks']}),flush=True)

def files(volume):
 return json.loads(command('docker','run','--rm','-v',volume+':/data:ro','python:3.12.12-slim','python','-c',"import os,json; print(json.dumps({os.path.relpath(os.path.join(p,f),'/data'):os.path.getsize(os.path.join(p,f)) for p,ds,fs in os.walk('/data') for f in fs}))"))

def prometheus():
 now=time.time();samples={'expired':now-8*86400,'fresh':now-3600}
 fixture=ROOT/'.deploy/retention'; fixture.mkdir(parents=True,exist_ok=True)
 for cohort,ts in samples.items():
  f=fixture/(cohort+'.openmetrics');f.write_text('# TYPE retention_probe gauge\nretention_probe{cohort="'+cohort+'"} 1 '+str(ts)+'\n# EOF\n')
  out=command('docker','run','--rm','--user','0','--entrypoint','/bin/promtool','-v',VOLUME_PREFIX+'prometheus-data:/prometheus','-v',str(fixture.resolve())+':/fixtures:ro','prom/prometheus:v3.8.0','tsdb','create-blocks-from','openmetrics','/fixtures/'+f.name,'/prometheus')
  (ART/('promtool-'+cohort+'.log')).write_text(out)
 command(*COMPOSE,'up','-d','prometheus',env={**os.environ,'RETENTION_PROM':'30d'})
 def query(cohort):
  params=urllib.parse.urlencode({'query':'retention_probe{cohort="'+cohort+'"}','time':samples[cohort]})
  return req('http://127.0.0.1:19090/api/v1/query?'+params)['data']['result']
 wait(lambda:query('expired') and query('fresh'))
 before=files(VOLUME_PREFIX+'prometheus-data'); (ART/'prometheus-before.json').write_text(json.dumps(before,indent=2))
 command(*COMPOSE,'up','-d','--force-recreate','prometheus',env={**os.environ,'RETENTION_PROM':'7d'})
 wait(lambda:not query('expired') and query('fresh'))
 after=files(VOLUME_PREFIX+'prometheus-data')
 removed={k:v for k,v in before.items() if k not in after and '/chunks/' in k}
 assert removed,'No expired block chunk removed'
 REPORT['checks']['prometheus']={'status':'passed','old_query_empty':True,'fresh_query_present':True,'expired_chunk_bytes_removed':sum(removed.values()),'removed_files':list(removed)};save()

def span(trace_id,cohort,ts):
 return {'resourceSpans':[{'resource':{'attributes':[{'key':'service.name','value':{'stringValue':'retention-'+cohort}}]},'scopeSpans':[{'spans':[{'traceId':trace_id,'spanId':uuid.uuid4().hex[:16],'name':cohort,'kind':2,'startTimeUnixNano':str(int(ts*1e9)),'endTimeUnixNano':str(int((ts+.01)*1e9))}]}]}]}

def tempo_loki():
 command(*COMPOSE,'up','-d','tempo','loki')
 wait(lambda:req('http://127.0.0.1:13200/ready'))
 wait(lambda:req('http://127.0.0.1:13100/ready'))
 now=time.time();old=now-80*3600;ids={k:uuid.uuid4().hex for k in ['expired','fresh']}
 # Let a real Tempo block age; backdating spans does not age the stored block.
 req('http://127.0.0.1:14318/v1/traces',span(ids['expired'],'expired',now))
 wait(lambda:req('http://127.0.0.1:13200/api/traces/'+ids['expired']))
 req('http://127.0.0.1:13200/flush',method='POST')
 tempo_before=wait(lambda:(lambda d:d if any(k.endswith('/data.parquet') for k in d) else None)(files(VOLUME_PREFIX+'tempo-data')))
 (ART/'tempo-before.json').write_text(json.dumps(tempo_before,indent=2))
 REPORT['tempo_trace_ids']=ids;save()
 # Loki streams use only a bounded cohort label. Enough content to measure chunks.
 payload={'streams':[{'stream':{'service_name':'retention-probe','cohort':cohort},'values':[[str(int((ts+i*.001)*1e9)),cohort+' '+uuid.uuid4().hex*64] for i in range(100)]} for cohort,ts in [('expired',old),('fresh',now)]]}
 req('http://127.0.0.1:13100/loki/api/v1/push',payload)
 def logs(cohort):
  params=urllib.parse.urlencode({'query':'{service_name="retention-probe",cohort="'+cohort+'"}','start':str(int(((old if cohort=='expired' else now)-60)*1e9)),'end':str(int(((old if cohort=='expired' else now)+60)*1e9)),'limit':1000})
  return req('http://127.0.0.1:13100/loki/api/v1/query_range?'+params)['data']['result']
 wait(lambda:logs('expired') and logs('fresh'))
 req('http://127.0.0.1:13100/flush',method='POST')
 before=wait(lambda:(lambda d:d if any(k.startswith('chunks/fake/') for k in d) else None)(files(VOLUME_PREFIX+'loki-data')))
 (ART/'loki-before.json').write_text(json.dumps(before,indent=2))
 time.sleep(max(0,65-(time.time()-now)))
 req('http://127.0.0.1:14318/v1/traces',span(ids['fresh'],'fresh',time.time()))
 wait(lambda:req('http://127.0.0.1:13200/api/traces/'+ids['fresh']))
 def trace_absent():
  try:req('http://127.0.0.1:13200/api/traces/'+ids['expired']);return False
  except urllib.error.HTTPError as e:return e.code==404
 wait(lambda:trace_absent() and req('http://127.0.0.1:13200/api/traces/'+ids['fresh']),1200)
 old_parquet={k:v for k,v in tempo_before.items() if k.endswith('/data.parquet')}
 wait(lambda:all(k not in files(VOLUME_PREFIX+'tempo-data') for k in old_parquet),180)
 tempo_files=files(VOLUME_PREFIX+'tempo-data');(ART/'tempo-after.json').write_text(json.dumps(tempo_files,indent=2))
 REPORT['checks']['tempo']={'status':'passed','old_trace_404':True,'fresh_trace_present':True,'trace_ids':ids,'test_retention_seconds':60,'old_block_bytes_removed':sum(old_parquet.values())};save()
 # The uploader keeps local indexes for cache validity + three resync periods.
 # Allow that normal synchronization window after physical chunk deletion.
 wait(lambda:not logs('expired') and logs('fresh'),2400)
 def expired_chunks_removed():
  after=files(VOLUME_PREFIX+'loki-data');removed={k:v for k,v in before.items() if k.startswith('chunks/fake/') and k not in after}
  return {'after':after,'removed':removed} if removed else None
 deletion=wait(expired_chunks_removed,600)
 (ART/'loki-after.json').write_text(json.dumps(deletion['after'],indent=2))
 REPORT['checks']['loki']={'status':'passed','old_logs_empty':True,'fresh_logs_present':True,'chunk_bytes_removed':sum(deletion['removed'].values()),'removed_files':list(deletion['removed'])};save()


def resume_loki():
 global REPORT
 REPORT=json.loads((ART/'retention.json').read_text());REPORT['status']='running';REPORT['compose_project']=PROJECT;REPORT.pop('error',None);save()
 before=json.loads((ART/'loki-before.json').read_text())
 def logs(cohort):
  center=REPORT['started_at']-(80*3600 if cohort=='expired' else 0)
  params=urllib.parse.urlencode({'query':'{service_name="retention-probe",cohort="'+cohort+'"}','start':str(int((center-600)*1e9)),'end':str(int((center+600)*1e9)),'limit':1000})
  return req('http://127.0.0.1:13100/loki/api/v1/query_range?'+params)['data']['result']
 wait(lambda:not logs('expired') and logs('fresh'),2400)
 def deletion():
  after=files(VOLUME_PREFIX+'loki-data');removed={k:v for k,v in before.items() if k.startswith('chunks/fake/') and k not in after}
  return (after,removed) if removed else None
 after,removed=wait(deletion,600)
 (ART/'loki-after.json').write_text(json.dumps(after,indent=2))
 REPORT['checks']['loki']={'status':'passed','old_logs_empty':True,'fresh_logs_present':True,'chunk_bytes_removed':sum(removed.values()),'removed_files':list(removed)};save()

try:
 if '--resume-loki' in sys.argv:resume_loki()
 else:command(*COMPOSE,'create');prometheus();tempo_loki()
 REPORT['status']='passed'
except BaseException as error:
 REPORT.update(status='failed',error=type(error).__name__+': '+str(error));raise
finally:
 REPORT['ended_at']=time.time();save()
 for name in ['prometheus','tempo','loki']:
  (ART/(name+'.log')).write_text(command(*COMPOSE,'logs','--no-color',name))
