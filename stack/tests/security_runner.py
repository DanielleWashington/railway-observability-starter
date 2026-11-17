"""Private, bounded security test job. No command or secret-bearing HTTP endpoints."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading


def job():
    if not os.environ.get('SECURITY_MODE'):
        return
    run_id = os.environ['SECURITY_RUN_ID']
    try:
        result = subprocess.run([sys.executable, '-u', '/tests/security.py'],
                                capture_output=True, text=True, timeout=900)
        reports = [json.loads(p.read_text()) for p in Path('/artifacts').glob('security-*.json')]
        # Observations are separate bounded log records to avoid truncating the final result.
        for report in reports:
            for sample in report.pop('collector_observations', []):
                print(json.dumps({'event': 'security_observation', 'run_id': run_id, **sample}), flush=True)
        print(json.dumps({'event': 'security_complete', 'run_id': run_id,
                          'exit_code': result.returncode, 'reports': reports}), flush=True)
    except Exception as error:
        print(json.dumps({'event': 'security_failed', 'run_id': run_id,
                          'error_type': type(error).__name__}), flush=True)


class Health(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(200 if self.path == '/health' else 404)
        self.end_headers()
        self.wfile.write(b'{}')


if __name__ == '__main__':
    threading.Thread(target=job, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8080'))), Health).serve_forever()
