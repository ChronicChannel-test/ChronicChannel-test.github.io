#!/usr/bin/env python3
"""
CIC multi-repo dev server — run with: python3 serve.py
Serves multiple repos from a single port without moving any folders.
API calls to /api/aq/... are proxied to the production Cloudflare Workers.
"""

import datetime
import http.server
import json
import os
import socketserver
import urllib.request
import urllib.error
from urllib.parse import urlparse, unquote, parse_qs, urlencode

PORT = 8080

# Proxy /api/aq/... to production Cloudflare Workers
API_PROXY_PREFIX  = '/api/aq'
API_PROXY_TARGET  = 'https://cic-test.chronicillnesschannel.co.uk'
POSTCODE_PROXY_ROUTES = {
    '/api/postcode_suggest': '/v1/postcode_suggest',
    '/api/postcode_lookup': '/v1/postcode_lookup',
}

# URL prefix → absolute filesystem root (longest prefix matched first)
ROOTS = {
    '/uk-aq':         '/Users/mikehinford/Dropbox/Projects/CIC Website/CIC Air Quality Networks/CIC-UK-AQ Webpage/CIC-test-uk-aq-webpage',
    '/data-explorer': '/Users/mikehinford/Dropbox/Projects/CIC Website/CIC Data Explorer/CIC Data Explorer Mark 2/CIC-test-data-explorer-mk2',
    '/report':        '/Users/mikehinford/Dropbox/Projects/CIC Website/CIC Report Form/CIC-TEST-report-form',
    '/station-snapshot': '/Users/mikehinford/Dropbox/Projects/CIC Website/CIC Air Quality Networks/CIC-test-uk-aq Operations/CIC-test-uk-aq-ops/station_snapshot',
    '/':                 '/Users/mikehinford/Dropbox/Projects/CIC Website/ChronicChannel-Test Root Repo/ChronicChannel-test.github.io',
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

# Ops repo root (one level up from station_snapshot)
OPS_REPO_ROOT = os.path.dirname(ROOTS['/station-snapshot'].rstrip('/')) if '/station-snapshot' in ROOTS else None

# Cloudflare Access Service Token — loaded from local .env with fallback to UK-AQ .env
_env = {}
_env.update(_load_env_file(os.path.join(ROOTS['/uk-aq'], '.env')))
_env.update(_load_env_file(os.path.join(os.path.dirname(__file__), '.env')))
if OPS_REPO_ROOT:
    _env.update(_load_env_file(os.path.join(OPS_REPO_ROOT, '.env')))
CF_CLIENT_ID     = _env.get('CLOUDFLARE_ACCESS_CLIENT_ID', '')
CF_CLIENT_SECRET = _env.get('CLOUDFLARE_ACCESS_CLIENT_SECRET', '')
AQ_CACHE_BYPASS_SECRET = _env.get('UK_AQ_CACHE_BYPASS_SECRET', '')
POSTCODE_UPSTREAM_URL = _env.get(
    'UK_AQ_POSTCODE_LOOKUP_UPSTREAM_URL',
    'https://uk-aq-postcode-lookup-r2-api.michael-hinford.workers.dev',
)
EDGE_UPSTREAM_SECRET = _env.get('UK_AQ_EDGE_UPSTREAM_SECRET', '')
TURNSTILE_SITE_KEY = _env.get('UK_AQ_TURNSTILE_SITE_KEY', '')
TURNSTILE_PLACEHOLDER = "__UK_AQ_TURNSTILE_SITE_KEY__"

# Database connection URLs for the Station Snapshot endpoint
INGESTDB_DB_URL = _env.get('SUPABASE_DB_URL', '')
OBSAQIDB_DB_URL = _env.get('OBS_AQIDB_SUPABASE_DB_URL', '')
INGESTDB_SUPABASE_URL = _env.get('SUPABASE_URL', '')
INGESTDB_SERVICE_KEY = _env.get('SB_SECRET_KEY', '') or _env.get('SUPABASE_SERVICE_ROLE_KEY', '')
OBSAQIDB_SUPABASE_URL = _env.get('OBS_AQIDB_SUPABASE_URL', '')
OBSAQIDB_SERVICE_KEY = _env.get('OBS_AQIDB_SECRET_KEY', '') or _env.get('SBASE_HISTORY_SB_SECRET', '')
STATION_SNAPSHOT_MODE = (_env.get('STATION_SNAPSHOT_MODE', 'api') or 'api').strip().lower()
try:
    STATION_SNAPSHOT_MAX_ROWS = int(_env.get('STATION_SNAPSHOT_MAX_ROWS', '10000'))
except (TypeError, ValueError):
    STATION_SNAPSHOT_MAX_ROWS = 10000
STATION_SNAPSHOT_MAX_ROWS = max(100, STATION_SNAPSHOT_MAX_ROWS)

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

    def _proxy_request(self, upstream_base, upstream_path, extra_headers=None):
        upstream_url = upstream_base.rstrip('/') + upstream_path
        req_headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in _HOP_BY_HOP
        }
        req_headers['Host'] = urlparse(upstream_base).netloc
        if extra_headers:
            req_headers.update(extra_headers)

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
                if self.command != 'HEAD' and resp.status not in (204, 304):
                    payload = resp.read()
                    try:
                        self.wfile.write(payload)
                    except (BrokenPipeError, ConnectionResetError):
                        print(f'  [proxy] {self.command} {self.path} → client disconnected during response write')
        except urllib.error.HTTPError as e:
            body = b'' if self.command == 'HEAD' else e.read()
            is_not_modified = (e.code == 304)
            if is_not_modified:
                print(f'  [proxy] {self.command} {self.path} → {e.code}')
            else:
                print(f'  [proxy] {self.command} {self.path} → {e.code} upstream error')
                print(f'  [proxy] response: {body[:300]}')
            self.send_response(e.code)
            for key, val in e.headers.items():
                if key.lower() not in _HOP_BY_HOP:
                    self.send_header(key, val)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            if self.command != 'HEAD' and e.code not in (204, 304) and body:
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    print(f'  [proxy] {self.command} {self.path} → client disconnected during error response write')
        except urllib.error.URLError as e:
            print(f'  [proxy] {self.command} {self.path} → connection failed: {e.reason}')
            self.send_error(502, f'API proxy error: {e.reason}')

    def _proxy_api(self):
        """Forward /api/aq/... to the production Cloudflare Worker and relay the response."""
        extra_headers = {}
        if CF_CLIENT_ID and CF_CLIENT_SECRET:
            extra_headers['CF-Access-Client-Id'] = CF_CLIENT_ID
            extra_headers['CF-Access-Client-Secret'] = CF_CLIENT_SECRET
        # Trusted server-side header for local-dev bypass in the test worker.
        if AQ_CACHE_BYPASS_SECRET:
            extra_headers['X-CIC-Local-Dev-Token'] = AQ_CACHE_BYPASS_SECRET
        self._proxy_request(API_PROXY_TARGET, self.path, extra_headers)

    def _proxy_postcode_api(self):
        """Forward /api/postcode_* to the postcode lookup worker route."""
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        upstream_route = POSTCODE_PROXY_ROUTES.get(route)
        if not upstream_route:
            self.send_error(404)
            return
        upstream_path = upstream_route
        if parsed.query:
            upstream_path = f'{upstream_path}?{parsed.query}'
        extra_headers = {}
        if EDGE_UPSTREAM_SECRET:
            extra_headers['x-uk-aq-upstream-auth'] = EDGE_UPSTREAM_SECRET
        self._proxy_request(POSTCODE_UPSTREAM_URL, upstream_path, extra_headers)

    def _is_postcode_proxy_route(self):
        decoded_path = unquote(urlparse(self.path).path)
        return decoded_path in POSTCODE_PROXY_ROUTES

    # ── Config API ────────────────────────────────────────────────────────────

    def _serve_api_config(self):
        """Return JSON config for the Station Snapshot page, populated from .env."""
        import json
        config = {
            'edge_url': _env.get('EDGE_URL', ''),
            'default_station_id': _env.get('CLEANAIRSURB_ST_ID', ''),
            'default_station_ref': _env.get('CLEANAIRSURB_ST_REF', ''),
            'default_obs_limit': _env.get('STATION_SNAPSHOT_OBS_LIMIT', 'all'),
            'snapshot_mode': 'api' if STATION_SNAPSHOT_MODE != 'sql' else 'sql',
        }
        body = json.dumps(config).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _is_snapshot_route(self):
        return unquote(urlparse(self.path).path) == '/api/snapshot'

    def _normalize_snapshot_window(self, value):
        normalized = (value or '').strip().lower()
        if normalized in ('6h', '24h', '7d', '21d', '31d', '90d'):
            return normalized
        return '24h'

    def _window_bounds_utc(self, window):
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        if window == '6h':
            delta = datetime.timedelta(hours=6)
        elif window == '7d':
            delta = datetime.timedelta(days=7)
        elif window == '21d':
            delta = datetime.timedelta(days=21)
        elif window == '31d':
            delta = datetime.timedelta(days=31)
        elif window == '90d':
            delta = datetime.timedelta(days=90)
        else:
            delta = datetime.timedelta(hours=24)
        start = now_utc - delta
        return (
            start.isoformat().replace('+00:00', 'Z'),
            now_utc.isoformat().replace('+00:00', 'Z'),
        )

    def _parse_obs_limit(self, value):
        raw = (value or '').strip().lower()
        if not raw or raw == 'all':
            return None
        try:
            parsed = int(raw)
        except ValueError:
            return None
        if parsed <= 0:
            return None
        return parsed

    def _fetch_json(self, url, headers, timeout=45):
        request = urllib.request.Request(url, method='GET', headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode('utf-8')
            return json.loads(body) if body else None

    def _post_json(self, url, headers, payload, timeout=45):
        request = urllib.request.Request(
            url,
            method='POST',
            headers=headers,
            data=json.dumps(payload).encode('utf-8'),
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode('utf-8')
            return json.loads(body) if body else None

    def _augment_obs_aqidb_via_api(self, result, window_start_iso, window_end_iso, effective_limit):
        if not OBSAQIDB_SUPABASE_URL or not OBSAQIDB_SERVICE_KEY:
            result.setdefault('meta', {})['obs_aqidb_source'] = 'unavailable'
            return

        headers = {
            'apikey': OBSAQIDB_SERVICE_KEY,
            'Authorization': f'Bearer {OBSAQIDB_SERVICE_KEY}',
            'Accept': 'application/json',
            'Accept-Profile': 'uk_aq_public',
            'Content-Profile': 'uk_aq_public',
            'Content-Type': 'application/json',
        }
        rest_base = OBSAQIDB_SUPABASE_URL.rstrip('/') + '/rest/v1'
        timeseries_rows = result.get('timeseries') if isinstance(result.get('timeseries'), list) else []
        selected_ts = result.get('selected_timeseries_id')
        try:
            selected_ts_int = int(selected_ts) if selected_ts is not None else None
        except (TypeError, ValueError):
            selected_ts_int = None

        all_rows = []
        for row in timeseries_rows:
            if not isinstance(row, dict):
                continue
            try:
                connector_id = int(row.get('connector_id'))
                ts_id = int(row.get('id'))
            except (TypeError, ValueError):
                continue

            payload = {
                'p_connector_id': connector_id,
                'p_timeseries_id': ts_id,
                'p_start_utc': window_start_iso,
                'p_end_utc': window_end_iso,
                'p_since_ts': None,
                'p_limit': effective_limit,
            }
            try:
                points = self._post_json(
                    rest_base + '/rpc/uk_aq_rpc_observs_timeseries_window',
                    headers,
                    payload,
                )
            except Exception:
                continue
            if not isinstance(points, list):
                continue
            for point in points:
                if not isinstance(point, dict):
                    continue
                all_rows.append({
                    'connector_id': connector_id,
                    'timeseries_id': ts_id,
                    'observed_at': point.get('observed_at'),
                    'value': point.get('value'),
                })

        all_rows.sort(key=lambda row: str(row.get('observed_at') or ''), reverse=True)
        if len(all_rows) > effective_limit:
            all_rows = all_rows[:effective_limit]
        result['obs_aqidb_observations_all'] = all_rows

        if selected_ts_int is not None:
            selected_rows = [row for row in all_rows if row.get('timeseries_id') == selected_ts_int]
            if len(selected_rows) > effective_limit:
                selected_rows = selected_rows[:effective_limit]
            result['obs_aqidb_observations'] = selected_rows

        if selected_ts_int is not None:
            try:
                q_hourly = urlencode([
                    ('timeseries_id', f'eq.{selected_ts_int}'),
                    ('order', 'timestamp_hour_utc.desc'),
                    ('limit', str(effective_limit)),
                    ('select', '*'),
                ])
                result['obs_aqidb_timeseries_aqi_hourly'] = self._fetch_json(
                    rest_base + '/uk_aq_timeseries_aqi_hourly?' + q_hourly,
                    headers,
                ) or []
            except Exception:
                result['obs_aqidb_timeseries_aqi_hourly'] = []

            try:
                q_daily = urlencode([
                    ('timeseries_id', f'eq.{selected_ts_int}'),
                    ('order', 'observed_day.desc'),
                    ('limit', str(effective_limit)),
                    ('select', '*'),
                ])
                result['obs_aqidb_timeseries_aqi_daily'] = self._fetch_json(
                    rest_base + '/uk_aq_timeseries_aqi_daily?' + q_daily,
                    headers,
                ) or []
            except Exception:
                result['obs_aqidb_timeseries_aqi_daily'] = []

        result.setdefault('meta', {})['obs_aqidb_source'] = 'service_role_postgrest'

    def _serve_api_snapshot_via_postgrest(self, station_id, station_ref, timeseries_id, window, obs_limit):
        if not INGESTDB_SUPABASE_URL or not INGESTDB_SERVICE_KEY:
            raise RuntimeError('API mode requires SUPABASE_URL and SB_SECRET_KEY/SUPABASE_SERVICE_ROLE_KEY')

        headers = {
            'apikey': INGESTDB_SERVICE_KEY,
            'Authorization': f'Bearer {INGESTDB_SERVICE_KEY}',
            'Accept': 'application/json',
            'Accept-Profile': 'uk_aq_public',
            'Content-Profile': 'uk_aq_public',
            'Content-Type': 'application/json',
        }
        rest_base = INGESTDB_SUPABASE_URL.rstrip('/') + '/rest/v1'
        obs_limit_int = self._parse_obs_limit(obs_limit)
        # API-backed snapshot RPC currently supports 100 or 1000 rows.
        rpc_obs_limit = 1000 if (obs_limit_int is None or obs_limit_int >= 1000) else 100
        rpc_payload = {
            'p_station_id': int(station_id) if station_id else None,
            'p_station_ref': station_ref or None,
            'p_timeseries_id': int(timeseries_id) if timeseries_id else None,
            'p_window': window,
            'p_obs_limit': rpc_obs_limit,
        }

        snapshot = self._post_json(rest_base + '/rpc/uk_aq_station_snapshot', headers, rpc_payload)
        if not isinstance(snapshot, dict):
            raise RuntimeError('Unexpected snapshot response shape from ingest API')

        result = {
            'meta': snapshot.get('meta') if isinstance(snapshot.get('meta'), dict) else {},
            'station': snapshot.get('station') or {},
            'timeseries': snapshot.get('timeseries') if isinstance(snapshot.get('timeseries'), list) else [],
            'stations_checkpoints': snapshot.get('stations_checkpoints') if isinstance(snapshot.get('stations_checkpoints'), list) else [],
            'timeseries_checkpoints': snapshot.get('timeseries_checkpoints') if isinstance(snapshot.get('timeseries_checkpoints'), list) else [],
            'observations': snapshot.get('observations') if isinstance(snapshot.get('observations'), list) else [],
            'observations_all': [],
            'obs_aqidb_observations': [],
            'obs_aqidb_observations_all': [],
            'obs_aqidb_timeseries_aqi_hourly': [],
            'obs_aqidb_timeseries_aqi_daily': [],
            'selected_timeseries_id': snapshot.get('selected_timeseries_id'),
        }

        if station_id:
            result['meta']['station_id'] = station_id
        if station_ref:
            result['meta']['station_ref'] = station_ref
        result['meta']['timeseries_id'] = timeseries_id or result.get('selected_timeseries_id')
        result['meta']['window'] = window
        result['meta']['obs_limit'] = obs_limit or 'all'
        result['meta']['generated_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
        result['meta']['snapshot_mode'] = 'api'
        result['meta']['ingest_source'] = 'service_role_postgrest_rpc'

        window_start_iso = str(result['meta'].get('window_start') or '')
        window_end_iso = str(result['meta'].get('window_end') or '')
        if not window_start_iso or not window_end_iso:
            window_start_iso, window_end_iso = self._window_bounds_utc(window)
            result['meta']['window_start'] = window_start_iso
            result['meta']['window_end'] = window_end_iso

        effective_limit = obs_limit_int if obs_limit_int is not None else STATION_SNAPSHOT_MAX_ROWS
        self._augment_obs_aqidb_via_api(result, window_start_iso, window_end_iso, effective_limit)
        return result

    def _serve_api_snapshot(self):
        """Query both databases directly and return station snapshot JSON."""
        parsed = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        station_id = params.get('station_id', '').strip()
        station_ref = params.get('station_ref', '').strip()
        timeseries_id = params.get('timeseries_id', '').strip()
        window = self._normalize_snapshot_window(params.get('window', '24h'))
        obs_limit = params.get('obs_limit', 'all').strip()

        if not station_id and not station_ref:
            self.send_error(400, 'station_id or station_ref is required')
            return

        if STATION_SNAPSHOT_MODE != 'sql':
            try:
                result = self._serve_api_snapshot_via_postgrest(
                    station_id=station_id,
                    station_ref=station_ref,
                    timeseries_id=timeseries_id,
                    window=window,
                    obs_limit=obs_limit,
                )
                body = json.dumps(result, default=str).encode('utf-8')
                self._json_response(body)
                return
            except Exception as exc:
                print(f'  [snapshot] API mode failed, falling back to SQL mode: {exc}')

        result = {
            'meta': {
                'station_id': station_id,
                'station_ref': station_ref,
                'timeseries_id': timeseries_id,
                'window': window,
                'obs_limit': obs_limit,
                'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'),
            },
            'station': {},
            'timeseries': [],
            'stations_checkpoints': [],
            'timeseries_checkpoints': [],
            'observations': [],
            'observations_all': [],
            'obs_aqidb_observations': [],
            'obs_aqidb_observations_all': [],
            'obs_aqidb_timeseries_aqi_hourly': [],
            'obs_aqidb_timeseries_aqi_daily': [],
            'selected_timeseries_id': timeseries_id if timeseries_id else None,
        }

        if not INGESTDB_DB_URL or not OBSAQIDB_DB_URL:
            result['meta']['error'] = (
                'Database not configured. Set SUPABASE_DB_URL and '
                'OBS_AQIDB_SUPABASE_DB_URL in .env'
            )
            body = json.dumps(result).encode('utf-8')
            self._json_response(body)
            return

        try:
            import psycopg2
            import psycopg2.extras

            ingest_conn = psycopg2.connect(INGESTDB_DB_URL)
            obsaqi_conn = psycopg2.connect(OBSAQIDB_DB_URL)

            try:
                with ingest_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    # Build WHERE clause for station lookup
                    station_where = []
                    station_args = []
                    if station_id:
                        station_where.append('s.id = %s')
                        station_args.append(int(station_id))
                    if station_ref:
                        station_where.append('s.station_ref = %s')
                        station_args.append(station_ref)
                    station_clause = ' AND '.join(station_where) or 'TRUE'

                    # Station
                    cur.execute(f'SELECT * FROM uk_aq_core.stations s WHERE {station_clause} LIMIT 1', station_args)
                    row = cur.fetchone()
                    if row:
                        result['station'] = dict(row)
                        # If we found station by ref but no station_id was given, use the id
                        if not station_id and 'id' in row:
                            station_id = str(row['id'])

                    # Timeseries for this station
                    if station_id:
                        cur.execute(
                            'SELECT * FROM uk_aq_core.timeseries WHERE station_id = %s ORDER BY id',
                            (int(station_id),)
                        )
                        result['timeseries'] = [dict(r) for r in cur.fetchall()]

                    # Station checkpoints
                    if station_id:
                        cur.execute(
                            'SELECT * FROM uk_aq_raw.openaq_station_checkpoints WHERE station_id = %s ORDER BY last_observed_at DESC',
                            (int(station_id),)
                        )
                        result['stations_checkpoints'] = [dict(r) for r in cur.fetchall()]

                    # Timeseries checkpoints
                    if station_id:
                        cur.execute(
                            'SELECT * FROM uk_aq_raw.openaq_timeseries_checkpoints WHERE station_id = %s ORDER BY last_observed_at DESC',
                            (int(station_id),)
                        )
                        result['timeseries_checkpoints'] = [dict(r) for r in cur.fetchall()]

                    # Resolve timeseries_id if not given
                    ts_ids = [str(t['id']) for t in result['timeseries'] if 'id' in t]
                    selected_ts_id = timeseries_id if timeseries_id else (ts_ids[0] if ts_ids else None)
                    if selected_ts_id:
                        result['selected_timeseries_id'] = selected_ts_id

                    # Build ts_id list for observation queries
                    ts_id_list = [int(t['id']) for t in result['timeseries'] if 'id' in t]

                    # Observations (ingestdb) - selected timeseries
                    if selected_ts_id:
                        limit_clause = ''
                        if obs_limit not in ('', 'all'):
                            limit_clause = f'LIMIT {int(obs_limit)}'
                        cur.execute(
                            f'SELECT * FROM uk_aq_core.observations '
                            f'WHERE timeseries_id = %s '
                            f'ORDER BY observed_at DESC {limit_clause}',
                            (int(selected_ts_id),)
                        )
                        result['observations'] = [dict(r) for r in cur.fetchall()]

                    # Observations (ingestdb) - all station timeseries
                    if ts_id_list:
                        limit_clause = ''
                        if obs_limit not in ('', 'all'):
                            limit_clause = f'LIMIT {int(obs_limit)}'
                        placeholders = ','.join(['%s'] * len(ts_id_list))
                        cur.execute(
                            f'SELECT * FROM uk_aq_core.observations '
                            f'WHERE timeseries_id IN ({placeholders}) '
                            f'ORDER BY observed_at DESC {limit_clause}',
                            ts_id_list
                        )
                        result['observations_all'] = [dict(r) for r in cur.fetchall()]

                with obsaqi_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    # ObsAQIDB observations - selected timeseries
                    if selected_ts_id:
                        limit_clause = ''
                        if obs_limit not in ('', 'all'):
                            limit_clause = f'LIMIT {int(obs_limit)}'
                        try:
                            cur.execute(
                                f'SELECT * FROM uk_aq_observs.observations '
                                f'WHERE timeseries_id = %s '
                                f'ORDER BY observed_at DESC {limit_clause}',
                                (int(selected_ts_id),)
                            )
                            result['obs_aqidb_observations'] = [dict(r) for r in cur.fetchall()]
                        except Exception:
                            pass

                    # ObsAQIDB observations - all station timeseries
                    if ts_id_list:
                        limit_clause = ''
                        if obs_limit not in ('', 'all'):
                            limit_clause = f'LIMIT {int(obs_limit)}'
                        placeholders = ','.join(['%s'] * len(ts_id_list))
                        try:
                            cur.execute(
                                f'SELECT * FROM uk_aq_observs.observations '
                                f'WHERE timeseries_id IN ({placeholders}) '
                                f'ORDER BY observed_at DESC {limit_clause}',
                                ts_id_list
                            )
                            result['obs_aqidb_observations_all'] = [dict(r) for r in cur.fetchall()]
                        except Exception:
                            pass

                    # AQI hourly
                    if selected_ts_id:
                        limit_clause = ''
                        if obs_limit not in ('', 'all'):
                            limit_clause = f'LIMIT {int(obs_limit)}'
                        try:
                            cur.execute(
                                f'SELECT * FROM uk_aq_aqilevels.timeseries_aqi_hourly '
                                f'WHERE timeseries_id = %s '
                                f'ORDER BY timestamp_hour_utc DESC {limit_clause}',
                                (int(selected_ts_id),)
                            )
                            result['obs_aqidb_timeseries_aqi_hourly'] = [dict(r) for r in cur.fetchall()]
                        except Exception:
                            pass

                    # AQI daily
                    if selected_ts_id:
                        limit_clause = ''
                        if obs_limit not in ('', 'all'):
                            limit_clause = f'LIMIT {int(obs_limit)}'
                        try:
                            cur.execute(
                                f'SELECT * FROM uk_aq_aqilevels.timeseries_aqi_daily '
                                f'WHERE timeseries_id = %s '
                                f'ORDER BY observed_day DESC {limit_clause}',
                                (int(selected_ts_id),)
                            )
                            result['obs_aqidb_timeseries_aqi_daily'] = [dict(r) for r in cur.fetchall()]
                        except Exception:
                            pass

            finally:
                ingest_conn.close()
                obsaqi_conn.close()

        except ImportError:
            result['meta']['error'] = 'psycopg2 not installed. Run: pip install psycopg2-binary'
        except Exception as exc:
            result['meta']['error'] = str(exc)

        body = json.dumps(result, default=str).encode('utf-8')
        self._json_response(body)

    def _json_response(self, body):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def do_GET(self):
        decoded_path = unquote(urlparse(self.path).path)
        if decoded_path == '/api/config':
            self._serve_api_config()
            return
        if self._is_postcode_proxy_route():
            self._proxy_postcode_api()
            return
        if decoded_path.startswith(API_PROXY_PREFIX):
            self._proxy_api()
            return
        if self._is_snapshot_route():
            self._serve_api_snapshot()
            return
        if self._maybe_serve_uk_aq_html_with_turnstile():
            return
        super().do_GET()

    def do_HEAD(self):
        if self._is_postcode_proxy_route():
            self.send_error(405)
            return
        if unquote(urlparse(self.path).path).startswith(API_PROXY_PREFIX):
            self.send_error(405)
            return
        if self._maybe_serve_uk_aq_html_with_turnstile():
            return
        super().do_HEAD()

    def do_POST(self):
        if self._is_postcode_proxy_route():
            self._proxy_postcode_api()
            return
        if unquote(urlparse(self.path).path).startswith(API_PROXY_PREFIX):
            self._proxy_api()
        else:
            self.send_error(405)

    def do_OPTIONS(self):
        if self._is_postcode_proxy_route():
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, x-uk-aq-upstream-auth')
            self.end_headers()
            return
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
        print(f'  /station-snapshot/ → Station Snapshot')
        print(f'  /api/aq/...      → proxy → {API_PROXY_TARGET}')
        print(f'  /api/postcode_*  → proxy → {POSTCODE_UPSTREAM_URL}')
        if CF_CLIENT_ID:
            print(f'  CF service token  → loaded ({CF_CLIENT_ID[:12]}...)')
        else:
            print(f'  CF service token  → NOT FOUND (check .env)')
        if EDGE_UPSTREAM_SECRET:
            print(f'  Edge upstream key → loaded ({EDGE_UPSTREAM_SECRET[:10]}...)')
        else:
            print(f'  Edge upstream key → NOT FOUND (add UK_AQ_EDGE_UPSTREAM_SECRET)')
        if TURNSTILE_SITE_KEY:
            print(f'  Turnstile key      → loaded ({TURNSTILE_SITE_KEY[:10]}...)')
        else:
            print(f'  Turnstile key      → NOT FOUND (add UK_AQ_TURNSTILE_SITE_KEY)')
        print(f'  Station snapshot   → mode={STATION_SNAPSHOT_MODE}')
        if INGESTDB_SUPABASE_URL and INGESTDB_SERVICE_KEY:
            print(f'  Snapshot ingest API → configured')
        else:
            print(f'  Snapshot ingest API → NOT set (add SUPABASE_URL + SB_SECRET_KEY)')
        if OBSAQIDB_SUPABASE_URL and OBSAQIDB_SERVICE_KEY:
            print(f'  Snapshot obs API    → configured')
        else:
            print(f'  Snapshot obs API    → NOT set (add OBS_AQIDB_SUPABASE_URL + OBS_AQIDB_SECRET_KEY)')
        if INGESTDB_DB_URL:
            print(f'  IngestDB           → configured')
        else:
            print(f'  IngestDB           → NOT set (add SUPABASE_DB_URL)')
        if OBSAQIDB_DB_URL:
            print(f'  ObsAQIDB           → configured')
        else:
            print(f'  ObsAQIDB           → NOT set (add OBS_AQIDB_SUPABASE_DB_URL)')
        print(f'\nCtrl+C to stop.\n')
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\nServer stopped.')
