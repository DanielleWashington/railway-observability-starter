"""One bounded test per deployment; logs carry reports, no public command API."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json,os,subprocess,sys,threading


def job():
    if not os.environ.get('LOAD_ENABLED'): return
    try:
        result=subprocess.run([sys.executable,'-u','/tests/load.py'],timeout=int(os.environ.get('LOAD_DURATION','1800'))+600)
        print(json.dumps({'event':'load_runner_finished','exit_code':result.returncode}),flush=True)
    except Exception as error:
        print(json.dumps({'event':'load_runner_failed','type':type(error).__name__}),flush=True)


class Health(BaseHTTPRequestHandler):
    def log_message(self,*_): pass
    def do_GET(self):
        self.send_response(200 if self.path=='/health' else 404);self.end_headers();self.wfile.write(b'{}')


if __name__=='__main__':
    threading.Thread(target=job,daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','8080'))),Health).serve_forever()
