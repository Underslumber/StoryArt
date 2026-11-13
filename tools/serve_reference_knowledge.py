"""Loopback-only read-only viewer; serves only generated catalog and listed images."""
from __future__ import annotations
import argparse
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.reference_knowledge import DEFAULT, load
from tools.reference_pool_manager import safe, sha


def routes(directory):
    data=load(directory)
    prefix='/'+directory.relative_to(ROOT).as_posix()+'/'
    allowed={prefix+'index.html':(directory/'index.html',None)}
    allowed['/']=allowed[prefix+'index.html']
    for page in data['collages']:
        allowed[prefix+page['path']]=(safe(directory,page['path']),page['sha256'])
    for r in data['records']:
        if r['preview_allowed'] and r.get('path'):
            allowed['/'+r['path']]=(safe(ROOT,r['path']),r['sha256'])
    return allowed


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=int,default=18840)
    ap.add_argument('--directory',type=Path,default=DEFAULT);a=ap.parse_args()
    allowed=routes(a.directory.resolve())
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if urlsplit(self.path).path=='/':
                self.send_response(302)
                self.send_header('Location','/'+a.directory.resolve().relative_to(ROOT).as_posix()+'/index.html')
                self.end_headers();return
            found=allowed.get(unquote(urlsplit(self.path).path))
            if not found: self.send_error(404);return
            p,expected=found
            if not p.is_file() or (expected and sha(p)!=expected): self.send_error(409,'Source changed; rebuild catalog');return
            body=p.read_bytes();self.send_response(200)
            self.send_header('Content-Type',mimetypes.guess_type(p.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers();self.wfile.write(body)
        def log_message(self,*args): pass
    print(f'http://127.0.0.1:{a.port}/work/reference-knowledge-20261113/index.html',flush=True)
    ThreadingHTTPServer(('127.0.0.1',a.port),Handler).serve_forever()


if __name__=='__main__':main()
