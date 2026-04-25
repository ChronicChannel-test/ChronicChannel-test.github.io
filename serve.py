#!/usr/bin/env python3
"""
CIC multi-repo dev server — run with: python3 serve.py
Serves multiple repos from a single port without moving any folders.
API calls to /api/aq/... are proxied to the production Cloudflare Workers.
"""

import http.server
import os
import socketserver
import urllib.request
import urllib.error
from urllib.parse import urlparse, unquote

PORT = 8080

# Proxy /api/aq/... to production Cloudflare Workers
API_PROXY_PREFIX  = '/api/aq'
API_PROXY_TARGET  = 'https://cic-test.chronicillnesschannel.co.uk'

# URL prefix → absolute filesystem root (longest prefix matched first)
ROOTS = {
    '/uk-aq':         '/Users/mikehinford/Dropbox/Projects/CIC Website/CIC Air Quality Networks/CIC UK-AQ Webpage/CIC-test-uk-aq',
    '/data-explorer': '/Users/mikehinford/Dropbox/Projects/CIC Website/CIC Data Explorer/CIC Data Explorer Mark 2/CIC-test-data-explorer-mk2',
    '/report':        '/Users/mikehinford/Dropbox/Projects/CIC Website/CIC Report Form/CIC-TEST-report-form',
    '/':              '/Users/mikehinford/Dropbox/Projects/CIC Website/ChronicChannel-Test Root Repo/ChronicChannel-test.github.io',
}


def _load_env_file(env_path):
    env = {}
    try:
        with open(env_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, _, v = line.partition('=')
                    env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env

# Cloudflare Access Service Token — loaded from local .env with fallback to UK-AQ .env
_env = {}
_env.update(_load_env_file(os.path.join(ROOTS['/uk-aq'], '.env')))
_env.update(_load_env_file(os.path.join(os.path.dirname(__file__), '.env')))
CF_CLIENT_ID     = _env.get('CLOUDFLARE_ACCESS_CLIENT_ID', '')
CF_CLIENT_SECRET = _env.get('CLOUDFLARE_ACCESS_CLIENT_SECRET', '')
AQ_CACHE_BYPASS_SECRET = _env.get('UK_AQ_CACHE_BYPASS_SECRET', '')
TURNSTILE_SITE_KEY = _env.get('UK_AQ_TURNSTILE_SITE_KEY', '')
TURNSTILE_PLACEHOLDER = "__UK_AQ_TURNSTILE_SITE_KEY__"

# Headers that must not be forwarded to the upstream or back to the client
_HOP_BY_HOP = frozenset([
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'te', 'trailers', 'transfer-encoding', 'upgrade',
    'host',  # we set this ourselves
])


class MultiRootHandler(http.server.SimpleHTTPRequestHandler):

    # ── API proxy ──────────────────────────────────────────────────────────────

    def _maybe_serve_uk_aq_html_with_turnstile(self):
        # Keep local URLs clean by replacing the Turnstile placeholder in served HTML.
        if not TURNSTILE_SITE_KEY:
            return False

        decoded_path = unquote(urlparse(self.path).path)
        if not decoded_path.startswith('/uk-aq'):
            return False

        target = self.translate_path(self.path)
        if not target.lower().endswith('.html') or not os.path.isfile(target):
            return False

        try:
            with open(target, 'rb') as f:
                source = f.read()
            html = source.decode('utf-8')
        except (OSError, UnicodeDecodeError):
            return False

        if TURNSTILE_PLACEHOLDER not in html:
            return False

        rendered = html.replace(TURNSTILE_PLACEHOLDER, TURNSTILE_SITE_KEY).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(rendered)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(rendered)
        return True

    def _proxy_api(self):
        """Forward /api/aq/... to the production Cloudflare Worker and relay the response."""
        upstream_url = API_PROXY_TARGET + self.path  # preserves path + query string
        req_headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in _HOP_BY_HOP
        }
        req_headers['Host'] = urlparse(API_PROXY_TARGET).netloc
        if CF_CLIENT_ID and CF_CLIENT_SECRET:
            req_headers['CF-Access-Client-Id']     = CF_CLIENT_ID
            req_headers['CF-Access-Client-Secret'] = CF_CLIENT_SECRET
        # Trusted server-side header for local-dev bypass in the test worker.
        if AQ_CACHE_BYPASS_SECRET:
            req_headers['X-CIC-Local-Dev-Token'] = AQ_CACHE_BYPASS_SECRET

        body = None
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length:
            body = self.rfile.read(content_length)

        req = urllib.request.Request(upstream_url, data=body,
                                     headers=req_headers, method=self.command)
        try:
            with urllib.request.urlopen(req) as resp:
                print(f'  [proxy] {self.command} {self.path} → {resp.status}')
                self.send_response(resp.status)
                for key, val in resp.headers.items():
                    if key.lower() not in _HOP_BY_HOP:
                        self.send_header(key, val)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read()
            print(f'  [proxy] {self.command} {self.path} → {e.code} upstream error')
            print(f'  [proxy] response: {body[:300]}')
            self.send_response(e.code)
            self.end_headers()
            self.wfile.write(body)
        except urllib.error.URLError as e:
            print(f'  [proxy] {self.command} {self.path} → connection failed: {e.reason}')
            self.send_error(502, f'API proxy error: {e.reason}')


    def do_GET(self):
        if unquote(urlparse(self.path).path).startswith(API_PROXY_PREFIX):
            self._proxy_api()
            return
        if self._maybe_serve_uk_aq_html_with_turnstile():
            return
        super().do_GET()

    def do_HEAD(self):
        if unquote(urlparse(self.path).path).startswith(API_PROXY_PREFIX):
            self.send_error(405)
            return
        if self._maybe_serve_uk_aq_html_with_turnstile():
            return
        super().do_HEAD()

    def do_POST(self):
        if unquote(urlparse(self.path).path).startswith(API_PROXY_PREFIX):
            self._proxy_api()
        else:
            self.send_error(405)

    def do_OPTIONS(self):
        if unquote(urlparse(self.path).path).startswith(API_PROXY_PREFIX):
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
            self.end_headers()
        else:
            self.send_error(405)

    # ── Static file serving ────────────────────────────────────────────────────

    def translate_path(self, path):
        # Decode percent-encoding (%20 → space etc.) and strip query/fragment
        clean = unquote(urlparse(path).path)

        # Match longest prefix first; handle '/' last so it doesn't swallow everything
        for prefix, root in sorted(ROOTS.items(), key=lambda x: -len(x[0])):
            if prefix == '/':
                # Root matches any remaining path
                relative = clean.lstrip('/')
            elif clean == prefix or clean.startswith(prefix + '/'):
                relative = clean[len(prefix):].lstrip('/')
            else:
                continue

            target = os.path.join(root, relative) if relative else root
            if os.path.isdir(target):
                target = os.path.join(target, 'index.html')
            return target

        # Fallback: serve from root repo
        return os.path.join(ROOTS['/'], clean.lstrip('/'))

    def log_message(self, fmt, *args):
        print(f'  {self.address_string()}  {fmt % args}')


if __name__ == '__main__':
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(('', PORT), MultiRootHandler) as httpd:
        print(f'\nCIC dev server → http://localhost:{PORT}/\n')
        print(f'  /                → CIC root site')
        print(f'  /uk-aq/          → UK-AQ repo')
        print(f'  /data-explorer/  → Data Explorer mk2')
        print(f'  /report/         → Report Form')
        print(f'  /api/aq/...      → proxy → {API_PROXY_TARGET}')
        if CF_CLIENT_ID:
            print(f'  CF service token  → loaded ({CF_CLIENT_ID[:12]}...)')
        else:
            print(f'  CF service token  → NOT FOUND (check .env)')
        if TURNSTILE_SITE_KEY:
            print(f'  Turnstile key      → loaded ({TURNSTILE_SITE_KEY[:10]}...)')
        else:
            print(f'  Turnstile key      → NOT FOUND (add UK_AQ_TURNSTILE_SITE_KEY)')
        print(f'\nCtrl+C to stop.\n')
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\nServer stopped.')
