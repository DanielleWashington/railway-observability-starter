"""Run a bounded acceptance job on deploy, with private health and log-only results."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

MODES = {
    'e2e': [], 'alerts': ['--alerts'], 'restart': ['--verify-restart'],
    'enqueue': ['--enqueue'], 'queue-recovery': ['--verify-queue'],
    'collector-firing': ['--collector-firing'], 'collector-resolved': ['--collector-resolved'],
}
INPUT_FILES = {'e2e.json', 'recovery.json', 'collector-incident.json'}


def job():
    mode = os.environ.get('ACCEPTANCE_MODE')
    if not mode:
        return
    run_id = os.environ['ACCEPTANCE_RUN_ID']
    try:
        if mode not in MODES:
            raise ValueError('Unknown acceptance mode')
        artifacts = Path('/artifacts')
        artifacts.mkdir(exist_ok=True)
        for name, value in json.loads(os.environ.get('ACCEPTANCE_INPUT_JSON', '{}')).items():
            if name not in INPUT_FILES:
                raise ValueError('Unexpected input artifact')
            (artifacts / name).write_text(json.dumps(value))
        env = {**os.environ, 'REPORT_PATH': str(artifacts / (mode + '.json'))}
        print(json.dumps({'event': 'acceptance_started', 'run_id': run_id, 'mode': mode}), flush=True)
        result = subprocess.run([sys.executable, '-u', '/tests/e2e.py', *MODES[mode]],
                                env=env, capture_output=True, text=True, timeout=900)
        if result.stdout:
            print(result.stdout, flush=True)
        if result.stderr:
            print(result.stderr, flush=True)
        outputs = {p.name: json.loads(p.read_text()) for p in artifacts.glob('*.json')
                   if p.name in INPUT_FILES or p.name == mode + '.json'}
        print(json.dumps({'event': 'acceptance_complete', 'run_id': run_id, 'mode': mode,
                          'exit_code': result.returncode, 'artifacts': outputs}), flush=True)
    except Exception as error:
        print(json.dumps({'event': 'acceptance_error', 'run_id': run_id, 'mode': mode,
                          'error': type(error).__name__ + ': ' + str(error)}), flush=True)


class Health(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        body = b'{"status":"ok"}' if self.path == '/health' else b'{}'
        self.send_response(200 if self.path == '/health' else 404)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    threading.Thread(target=job, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8080'))), Health).serve_forever()
