"""Botkeep API client: login, register (via proxy), verify, server ladder."""
import json
import http.cookiejar
import urllib.request, urllib.error, urllib.parse
from .config import BK, UA, STATE, save_state, SPEK_LADDER
from .solver import solve_turnstile
from .providers import pool_proxies

# ---------------- botkeep client ----------------
def bk_login(email, password):
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    body = json.dumps({'email': email, 'password': password}).encode()
    req = urllib.request.Request(BK + '/auth/login', data=body, headers={
        'Content-Type': 'application/json', 'User-Agent': UA,
        'Origin': 'https://botkeep.cloud'})
    try:
        user = json.loads(op.open(req, timeout=30).read()).get('user', {})
    except urllib.error.HTTPError as e:
        return None, f'login {e.code}'
    cookies = '; '.join(f'{c.name}={c.value}' for c in cj)
    req2 = urllib.request.Request(BK + '/auth/session',
                                  headers={'Cookie': cookies, 'User-Agent': UA})
    csrf = json.loads(op.open(req2, timeout=30).read()).get('csrf_token')
    return {'cookies': cookies, 'csrf': csrf, 'email': email,
            'user_id': user.get('id')}, None


def bk_sess_call(sess, path, method='GET', body=None, use_csrf=False):
    if not sess:
        return {'_err': 401, 'detail': 'no session'}
    hdrs = {'Cookie': sess['cookies'], 'User-Agent': UA, 'Origin': 'https://botkeep.cloud'}
    if body is not None:
        hdrs['Content-Type'] = 'application/json'
    if use_csrf:
        hdrs['X-CSRF-Token'] = sess['csrf']
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BK + path, data=data, headers=hdrs, method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=8).read())
    except urllib.error.HTTPError as e:
        try:
            return {'_err': e.code, 'detail': json.loads(e.read()).get('detail', '')}
        except Exception:
            return {'_err': e.code, 'detail': ''}
    except Exception as e:
        return {'_err': 0, 'detail': str(e)}


def bk(path, method='GET', body=None, use_csrf=False):
    return bk_sess_call(STATE.get('session'), path, method, body, use_csrf)


def _proxy_opener(proxy):
    """Opener yang lewat proxy http://... (buat POST register WAF-blocked)."""
    ph = urllib.request.ProxyHandler({'http': proxy, 'https': proxy})
    return urllib.request.build_opener(ph)


def bk_register(display_name, email, password, proxy=None):
    tok, err = solve_turnstile('register', proxy)
    if not tok:
        return None, f'turnstile: {err}'
    body = json.dumps({
        'display_name': display_name, 'email': email, 'password': password,
        'legal_accepted': True, 'captcha_token': tok}).encode()
    last_err = None
    # WAJIB lewat proxy (direct = 429 WAF). Coba proxy + 1 backup dari pool.
    tried = []
    if proxy:
        tried.append(proxy)
    if job_pool_backup(tried):
        pass
    for px in tried:
        op = _proxy_opener(f'http://{px}')
        req = urllib.request.Request(BK + '/auth/register', data=body, headers={
            'Content-Type': 'application/json', 'User-Agent': UA,
            'Origin': 'https://botkeep.cloud'})
        try:
            d = json.loads(op.open(req, timeout=20).read())
            return d.get('user', {}), None
        except urllib.error.HTTPError as e:
            try:
                det = json.loads(e.read()).get('detail', '')
            except Exception:
                det = ''
            last_err = f'register {e.code}{" via " + px.split(":")[0] if px else ""}: {det}'
            if e.code == 400:
                return None, last_err  # validasi/product error — proxy lain percuma
        except Exception as e:
            last_err = f'register {str(e)[:40]} via proxy'
    return None, last_err


def job_pool_backup(already):
    """Tambah 1 proxy fresh dari pool buat backup attempt register."""
    try:
        pool = pool_proxies()
        for p in pool[::-1]:
            if p not in already:
                already.append(p)
                return True
    except Exception:
        pass
    return False


def bk_verify_email(token):
    body = json.dumps({'token': token}).encode()
    req = urllib.request.Request(BK + '/auth/verify', data=body, headers={
        'Content-Type': 'application/json', 'User-Agent': UA,
        'Origin': 'https://botkeep.cloud'})
    try:
        urllib.request.urlopen(req, timeout=30)
        return True, None
    except urllib.error.HTTPError as e:
        return False, f'verify {e.code}'



import urllib.parse


def build_payload(spek):
    return {
        "name": "iris-worker", "platform": "general",
        "runtime": "python", "runtimeVersion": "3.12",
        "resources": {"memoryLimitMb": spek['mem'],
                      "cpuLimitPercent": spek['cpu'],
                      "storageLimitMb": spek['disk']},
        "startCommand": "python main.py", "sourceType": "blank",
        "seedFiles": [{"path": "/main.py",
                       "content": "print('iris botkeep worker online')\n"}],
    }


def try_create_server():
    """Coba create server pake ladder spek. Return (result_dict, spek, error)."""
    w = STATE['watch']
    for li in range(w.get('spek_idx', 0), len(SPEK_LADDER)):
        spek = SPEK_LADDER[li]
        p = build_payload(spek)
        q = urllib.parse.urlencode({
            'runtime': p['runtime'], 'runtimeVersion': p['runtimeVersion'],
            'memoryLimitMb': spek['mem'], 'cpuLimitPercent': spek['cpu'],
            'storageLimitMb': spek['disk']})
        av = bk(f'/panel/creation-availability?{q}')
        if av.get('_err'):
            return None, spek, f"availability err {av.get('_err')}"
        if not av.get('allowed'):
            return None, spek, av.get('message') or av.get('detail') or 'no capacity'
        res = bk('/panel/bots', method='POST', body=p, use_csrf=True)
        if res.get('id'):
            return res, spek, None
        det = res.get('detail') or str(res)[:60]
        # kalau "select fewer resources" -> turun ladder, coba lagi
        w['spek_idx'] = li + 1
        save_state()
    return None, SPEK_LADDER[-1], 'semua spek ditolak'


def assign_server(account, res, spek):
    account['server'] = {'id': res.get('id'), 'name': res.get('name'),
                         'status': res.get('status', 'created'),
                         'runtime': f"{res.get('runtime', 'python')} {res.get('runtimeVersion', '3.12')}",
                         'spek': f"{spek['mem']}MB/{spek['cpu']}%/{spek['disk']}MB"}


