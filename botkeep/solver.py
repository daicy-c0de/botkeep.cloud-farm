"""Turnstile solver (solver sidecar lokal)."""
import os, json
import urllib.request

# ---------------- turnstile solver ----------------
def solver_key():
    return os.environ.get('SOLVER_KEY', '')


def solve_turnstile(action='register', proxy=None):
    key = solver_key()
    if not key:
        return None, 'solver key ga ketemu'
    payload = {'url': os.environ.get('TARGET_URL', 'https://botkeep.cloud/register'),
               'sitekey': os.environ.get('SOLVER_SITEKEY', ''),
               'action': action}
    if proxy:
        payload['proxy'] = proxy
    req = urllib.request.Request(os.environ.get('SOLVER_URL', 'http://localhost:8790/solve'),
                                 data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json',
                                          'Authorization': 'Bearer ' + key})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=180).read())
        if d.get('token'):
            return d['token'], None
        return None, d.get('error', 'no token')
    except Exception as e:
        return None, str(e)


