"""Minimal Dimensions HTTP transport following the official API v2 protocol.

Credentials and JWT tokens stay in memory. Neither is returned in exceptions,
query logs, manifests or caches. Successful query JSON alone may be cached.
"""
from __future__ import annotations
from types import SimpleNamespace
from urllib.parse import urlparse
import time
import requests
from .io import InputError

class HttpDsl:
    def __init__(self, key: str, endpoint: str, timeout: float = 180, session=None):
        if not key or key.strip().upper() == 'XXXXXXX':
            raise InputError('Replace XXXXXXX in the local .env file with your Dimensions API key.')
        parsed = urlparse(endpoint)
        if (parsed.scheme != 'https' or not parsed.hostname or
                not parsed.hostname.endswith('.dimensions.ai') or parsed.username or parsed.password or
                parsed.path not in {'','/'} or parsed.query or parsed.fragment):
            raise InputError('DIMENSIONS_ENDPOINT must be an HTTPS Dimensions base URL, e.g. https://app.dimensions.ai.')
        self.key = key
        self.endpoint = endpoint.rstrip('/')
        self.timeout = timeout
        self.session = session or requests.Session()
        self.token = None
        self.authenticated_at = 0.0

    def _authenticate(self):
        try:
            response = self.session.post(self.endpoint + '/api/auth', json={'key': self.key},
                                         timeout=self.timeout, allow_redirects=False)
        except requests.RequestException:
            raise InputError('Dimensions authentication connection failed; inspect network/proxy settings.') from None
        if response.status_code in {401,403}:
            raise InputError('Dimensions rejected authentication; check your API key and Analytics API entitlement.')
        if response.status_code != 200:
            raise InputError(f'Dimensions authentication HTTP {response.status_code}; no credentials were logged.')
        try:
            token = response.json().get('token')
        except ValueError:
            raise InputError('Dimensions authentication returned invalid JSON.') from None
        if not isinstance(token,str) or not token:
            raise InputError('Dimensions did not return an authentication token.')
        self.token = token
        self.authenticated_at = time.monotonic()

    def query(self, query):
        if self.token is None or time.monotonic() - self.authenticated_at > 6500:
            self._authenticate()
        for attempt in range(2):
            try:
                response = self.session.post(
                    self.endpoint + '/api/dsl/v2', data=query.encode('utf-8'),
                    headers={'Authorization': 'JWT ' + self.token,
                             'Content-Type': 'text/plain; charset=utf-8'},
                    timeout=self.timeout, allow_redirects=False)
            except requests.RequestException:
                raise InputError('Dimensions query connection failed; cached successful pages are retained.') from None
            if response.status_code == 401 and attempt == 0:
                self._authenticate()
                continue
            if response.status_code == 429:
                retry = response.headers.get('Retry-After','60')
                try: wait = min(300,max(60,float(retry)))
                except ValueError: wait = 60
                time.sleep(wait)
                raise InputError('Dimensions rate limit reached; the request will be retried by the cache client.')
            if response.status_code != 200:
                raise InputError(f'Dimensions query HTTP {response.status_code}; check entitlement/schema/service status.')
            try: obj = response.json()
            except ValueError:
                raise InputError('Dimensions query returned invalid JSON.') from None
            if not isinstance(obj,dict):
                raise InputError('Dimensions returned a non-object response.')
            return SimpleNamespace(json=obj, errors=obj.get('errors') or obj.get('_errors'))
        raise InputError('Dimensions token refresh failed.')
