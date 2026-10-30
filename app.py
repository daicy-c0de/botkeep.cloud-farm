#!/usr/bin/env python3
"""@kuromistorex_bot — Botkeep Manager v5

Buat Akun (max 5 thread), live log per-thread:
  🎧 Buat Akun — live log
  Proxy : 🏭 pool (536 available)
  Target: 3 akun · 3 thread
  Progress: ✅ 1 · ❌ 0 · ⏳ 2
  🧩 T1 email|password|status

Autoretry server, live log per-akun:
  🔁 Autoretry — live log
  Total Akun : 2
  Akun 1 : email|spek server|data server/status
"""
import os, json, time, re, secrets, string, threading, subprocess, queue
import urllib.request, urllib.parse, http.cookiejar

# ---------------- config (env / .env) ----------------
def _load_env(path='.env'):
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

STATE_DIR = os.environ.get('STATE_DIR', '.')
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
API = f'https://api.telegram.org/bot{BOT_TOKEN}'
BK = os.environ.get('BK_API', 'https://api.botkeep.cloud/api/v1')
AUTH_USERS = {int(x) for x in os.environ.get('AUTH_USERS', '0').split(',') if x.strip()}
STATE_FILE = os.environ.get('STATE_FILE', os.path.join(STATE_DIR, 'state.json'))
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
MAX_THREADS = 3      # = solver concurrency (SOLVER_MAX_CONC=3), 5 bikin antrian 502
MAX_ATTEMPTS = 8     # per slot: nyerah setelah ini (jadi REAL fail)
SOLVE_BACKOFF = 25   # detik antar retry solve (kecilin beban solver)
WATCH_INTERVAL = 300

# ladder spek: coba dari paling gemuk, turun kalau ditolak capacity
SPEK_LADDER = [
    {'mem': 512, 'cpu': 50, 'disk': 512},
    {'mem': 384, 'cpu': 40, 'disk': 384},
    {'mem': 256, 'cpu': 30, 'disk': 256},
    {'mem': 192, 'cpu': 20, 'disk': 192},
    {'mem': 128, 'cpu': 10, 'disk': 128},
]

DEFAULT = {
    "accounts": [],   # {email, mail_pwd, password, user_id, verified, created_at, server}
    "session": None,
    "watch": {"enabled": False, "last_check": 0, "attempts": 0,
              "last_error": None, "spek_idx": 0},
    "proxy_mode": "pool",
    "upload_proxies": [],
}


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            s = json.load(f)
        for k, v in DEFAULT.items():
            s.setdefault(k, json.loads(json.dumps(v)))
        return s
    return json.loads(json.dumps(DEFAULT))


def save_state():
    with open(STATE_FILE, 'w') as f:
        json.dump(STATE, f, indent=2)


STATE = load_state()
PENDING_UPLOAD = {}
JOBS = {}
DRAFT = {}


# ---------------- telegram (keep-alive, 1 koneksi TLS dipake ulang) ----------------
import http.client, ssl as _ssl
_ssl_ctx = _ssl.create_default_context()
_conn_lock = threading.Lock()
_conn = None


def _get_conn(timeout=35):
    global _conn
    if _conn is None:
        _conn = http.client.HTTPSConnection('api.telegram.org', context=_ssl_ctx,
                                            timeout=timeout)
    return _conn


def tg(method, payload=None, timeout=35):
    global _conn
    data = json.dumps(payload).encode() if payload is not None else None
    hdrs = {'Content-Type': 'application/json'}
    for attempt in (1, 2):
        try:
            with _conn_lock:
                c = _get_conn(timeout)
                c.timeout = timeout
                c.request('POST', f'/bot{BOT_TOKEN}/{method}', body=data, headers=hdrs)
                resp = c.getresponse()
                out = json.loads(resp.read())
                if resp.will_close:
                    _conn.close(); _conn = None
                return out
        except Exception:
            try:
                if _conn:
                    _conn.close()
            except Exception:
                pass
            _conn = None
            if attempt == 2:
                return {"ok": False, "error": "conn fail"}
    return {"ok": False, "error": "conn fail"}


def send(chat_id, text, kb=None):
    p = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML',
         'disable_web_page_preview': True}
    if kb:
        p['reply_markup'] = {'inline_keyboard': kb}
    return tg('sendMessage', p)


def edit(chat_id, msg_id, text, kb=None):
    """Edit message. GA ADA fallback send — kalau edit gagal, skip (anti spam)."""
    if not msg_id:
        return send(chat_id, text, kb)
    p = {'chat_id': chat_id, 'message_id': msg_id, 'text': text,
         'parse_mode': 'HTML', 'disable_web_page_preview': True}
    if kb:
        p['reply_markup'] = {'inline_keyboard': kb}
    r = tg('editMessageText', p)
    if not r.get('ok') and 'message is not modified' in str(r.get('description', '')):
        return {"ok": True}
    return r


def ack(cb_id, text=None, alert=False):
    p = {'callback_query_id': cb_id, 'show_alert': alert}
    if text:
        p['text'] = text
    return tg('answerCallbackQuery', p)


# ---------------- keyboards ----------------
KB_MAIN = [
    [{'text': '👤 Buat Akun Botkeep', 'callback_data': 'acc_start'}],
    [{'text': '📊 Akun Saya', 'callback_data': 'status'},
     {'text': '🖥 Server', 'callback_data': 'servers'}],
    [{'text': '🔁 Autoretry', 'callback_data': 'watch_menu'}],
    [{'text': '🏭 Proxy', 'callback_data': 'proxy_menu'}],
]
KB_PROXY = [
    [{'text': '🏭 Pool (proxysystem)', 'callback_data': 'acc_proxy_pool'}],
    [{'text': '📤 Upload File', 'callback_data': 'acc_proxy_upload'}],
    [{'text': '« Menu', 'callback_data': 'menu'}],
]
KB_COUNT = [
    [{'text': '1', 'callback_data': 'acc_n_1'},
     {'text': '3', 'callback_data': 'acc_n_3'},
     {'text': '5', 'callback_data': 'acc_n_5'}],
    [{'text': '10', 'callback_data': 'acc_n_10'},
     {'text': '20', 'callback_data': 'acc_n_20'}],
    [{'text': '« Ganti proxy', 'callback_data': 'acc_start'}],
]
KB_THREADS = []
_row = []
for i in range(1, MAX_THREADS + 1):
    _row.append({'text': f'{i} thread', 'callback_data': f'acc_t_{i}'})
    if len(_row) == 3:
        KB_THREADS.append(_row); _row = []
if _row:
    KB_THREADS.append(_row)
KB_THREADS.append([{'text': '« Ulang', 'callback_data': 'acc_start'}])
KB_BACK = [[{'text': '« Menu', 'callback_data': 'menu'}]]
KB_WATCH = [
    [{'text': '▶️ Aktifin', 'callback_data': 'watch_on'},
     {'text': '⏹ Stop', 'callback_data': 'watch_off'}],
    [{'text': '🔄 Cek Sekarang', 'callback_data': 'watch_check'}],
    [{'text': '« Menu', 'callback_data': 'menu'}],
]
KB_AFTER_ACC = [
    [{'text': '🔁 Aktifin Autoretry', 'callback_data': 'watch_on'}],
    [{'text': '📊 Akun Saya', 'callback_data': 'status'}],
    [{'text': '« Menu', 'callback_data': 'menu'}],
]


# ---------------- mail.tm ----------------
def mail_req(path, data=None, token=None):
    hdrs = {'Content-Type': 'application/json'}
    if token:
        hdrs['Authorization'] = 'Bearer ' + token
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request('https://api.mail.tm' + path, data=body, headers=hdrs,
                                 method='POST' if body is not None else 'GET')
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except urllib.error.HTTPError as e:
        return {'_err': e.code}


def mail_create():
    user = ''.join(secrets.choice(string.ascii_lowercase) for _ in range(8))
    pwd = secrets.token_urlsafe(14) + 'Aa1!'
    d = mail_req('/domains')
    domains = d if isinstance(d, list) else d.get('hydra:member', [])
    if not domains:
        return None
    addr = f"{user}@{domains[0]['domain']}"
    acc = mail_req('/accounts', {'address': addr, 'password': pwd})
    if acc.get('_err'):
        return None
    tok = mail_req('/token', {'address': addr, 'password': pwd})
    if tok.get('_err'):
        return None
    return {'address': addr, 'password': pwd, 'token': tok['token']}


def mail_wait_verification(mailbox, tries=15, gap=5):
    for _ in range(tries):
        d = mail_req('/messages', token=mailbox['token'])
        msgs = d if isinstance(d, list) else d.get('hydra:member', [])
        for m in msgs:
            frm = (m.get('from') or {}).get('address') or ''
            subj = m.get('subject') or ''
            if 'botkeep' in frm.lower() or 'verify' in subj.lower():
                full = mail_req('/messages/' + m['id'], token=mailbox['token'])
                body = full.get('text') or ''.join(full.get('html') or [])
                mt = re.search(r'verify=([A-Za-z0-9_\-\.]{20,})', body)
                if mt:
                    return mt.group(1)
        time.sleep(gap)
    return None


# ---------------- tempmail.plus (fallback provider) ----------------
TMPPLUS_UA = UA
TMPPLUS_DOMAINS = ['mailto.plus', 'fexpost.com']  # validated MX (mx.fex.plus)


def tmpplus_create():
    """Bikin inbox random di tempmail.plus. Domain di-rotasi biar ga gampang kedetek."""
    user = ''.join(secrets.choice(string.ascii_lowercase + string.digits)
                   for _ in range(10))
    dom = TMPPLUS_DOMAINS[secrets.randbelow(len(TMPPLUS_DOMAINS))]
    addr = f"{user}@{dom}"
    # "aktifkan" inbox dengan buka halaman es/ sekali
    try:
        req = urllib.request.Request(
            f'https://tempmail.plus/es/?email={addr}',
            headers={'User-Agent': TMPPLUS_UA, 'Referer': 'https://tempmail.plus/es/'})
        urllib.request.urlopen(req, timeout=15).read()
        return {'address': addr, 'password': '', 'token': None, 'provider': 'tmpplus'}
    except Exception:
        return None


def tmpplus_poll(address, min_time=None, tries=20, gap=5):
    """Poll inbox tempmail.plus, return (from, subject, body_html)."""
    import email as _em
    for _ in range(tries):
        try:
            url = (f'https://tempmail.plus/api/mails/?email={address}'
                   f'&first_id=0&limit=10')
            req = urllib.request.Request(url, headers={
                'User-Agent': TMPPLUS_UA, 'Referer': 'https://tempmail.plus/es/'})
            d = json.loads(urllib.request.urlopen(req, timeout=15).read())
            for m in d.get('mail_list', []):
                frm = (m.get('from_mail') or '').lower()
                subj = m.get('subject') or ''
                if 'botkeep' in frm or 'verify' in subj.lower():
                    # ambil full body
                    fr = urllib.request.Request(
                        f'https://tempmail.plus/api/mails/{m["mail_id"]}?email={address}',
                        headers={'User-Agent': TMPPLUS_UA,
                                 'Referer': 'https://tempmail.plus/es/'})
                    full = json.loads(urllib.request.urlopen(fr, timeout=15).read())
                    body = full.get('html') or full.get('text') or ''
                    if isinstance(body, list):
                        body = ''.join(body)
                    return frm, subj, body
        except Exception:
            pass
        time.sleep(gap)
    return None, None, None


# ---------------- generator.email (fallback provider #2, domain auto-ganti) ----------------
GEN_UA = UA


def gen_create(domain=None):
    """Bikin inbox generator.email — address bebas, domain disediain abu-abu (random tiap request)."""
    user = ''.join(secrets.choice(string.ascii_lowercase + string.digits)
                   for _ in range(9))
    # domain dari halaman utama (default yang lagi dipake generator)
    dom = domain
    if not dom:
        try:
            req = urllib.request.Request('https://generator.email/',
                                         headers={'User-Agent': GEN_UA})
            html = urllib.request.urlopen(req, timeout=15).read().decode(errors='ignore')
            m = re.search(r'([a-z0-9]+@[a-z0-9\-]+\.[a-z\.]{2,10})', html)
            dom = m.group(1).split('@')[1] if m else 'fatub.org'
        except Exception:
            dom = 'fatub.org'
    addr = f"{user}@{dom}"
    # "aktifkan" inbox — buka halamannya sekali, simpan cookie session
    try:
        req = urllib.request.Request(f'https://generator.email/{addr}',
                                     headers={'User-Agent': GEN_UA,
                                              'Referer': 'https://generator.email/'})
        urllib.request.urlopen(req, timeout=15).read()
        return {'address': addr, 'password': '', 'token': None,
                'provider': 'generator.email'}
    except Exception:
        return None


def gen_poll(address, tries=20, gap=5):
    """Poll inbox generator.email (HTML). Return body HTML email verif pertama."""
    for _ in range(tries):
        try:
            req = urllib.request.Request(
                f'https://generator.email/{address}',
                headers={'User-Agent': GEN_UA,
                         'Referer': 'https://generator.email/',
                         'Accept': 'text/html,application/xhtml+xml'})
            html = urllib.request.urlopen(req, timeout=15).read().decode(errors='ignore')
            if 'botkeep' in html.lower() or 'verify' in html.lower():
                return html
        except Exception:
            pass
        time.sleep(gap)
    return None


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


# ---------------- proxy ----------------
def pool_proxies():
    try:
        data = json.load(open(os.environ.get('POOL_FILE', 'proxies.json')))
        return [p['addr'] for p in data if p.get('https')]
    except Exception:
        return []


# ---------------- BUAT AKUN ----------------
ACC_STEPS = {
    'idle': '💤', 'email': '📧', 'solve': '🧩', 'register': '📝',
    'verif': '📨', 'gagal': '❌', 'jadi': '✅', 'selesai': '🏁',
}


STOP_JOBS = {}   # job_id -> True (sinyal stop buat worker + supervisor)


def acc_worker(wid, job):
    st = job['thread_state'][wid]
    q = job['queue']
    while True:
        if STOP_JOBS.get(job['id']):
            st['step'] = 'selesai'
            st['status'] = '⛔ di-stop'
            return
        try:
            idx = q.get_nowait()
        except queue.Empty:
            st['step'] = 'selesai'
            return
        st['idx'] = idx

        # RETRY LOOP: slot ini ga nyerah ampe akun jadi (max MAX_ATTEMPTS)
        attempt = 0
        while attempt < MAX_ATTEMPTS:
            if STOP_JOBS.get(job['id']):
                st['step'] = 'selesai'
                st['status'] = '⛔ di-stop'
                return
            attempt += 1
            retry_tag = f' (coba {attempt})' if attempt > 1 else ''

            # email baru tiap attempt — chain: generator.email -> mail.tm -> tempmail.plus
            st['step'] = 'email'
            st['email'] = ''
            st['pwd'] = ''
            st['status'] = f'bikin email temp...{retry_tag}'
            box = gen_create()
            provider = 'generator.email'
            if not box:
                st['status'] = f'generator.email gagal, fallback mail.tm...{retry_tag}'
                box = mail_create()
                provider = 'mail.tm'
            if not box:
                st['status'] = f'mail.tm limit, fallback tempmail.plus...{retry_tag}'
                box = tmpplus_create()
                provider = 'tempmail.plus'
            if not box:
                st['step'] = 'email'; st['email'] = ''
                st['status'] = f'semua provider gagal, retry 10s{retry_tag}'
                _sleep_stoppable(10, job)
                continue
            st['email'] = box['address']

            # turnstile
            st['step'] = 'solve'
            st['status'] = f'solve turnstile...{retry_tag}'
            proxy = None
            plist = job.get('proxies') or []
            if plist:
                proxy = plist[(idx + wid + attempt) % len(plist)]
            pwd = secrets.token_urlsafe(14) + 'Aa1!'
            st['pwd'] = pwd
            user, err = bk_register('iriskeeper', box['address'], pwd, proxy)
            if not user:
                st['step'] = 'solve'
                if 'turnstile' in (err or ''):
                    st['status'] = f'solver padat, retry {SOLVE_BACKOFF}s{retry_tag}'
                    _sleep_stoppable(SOLVE_BACKOFF, job)
                else:
                    st['status'] = f'{(err or "")[:40]}, retry 15s{retry_tag}'
                    _sleep_stoppable(15, job)
                continue
            st['step'] = 'register'
            st['status'] = f'daftar OK, login...{retry_tag}'

            # login + verif
            st['step'] = 'verif'
            st['status'] = f'login + kirim verif...{retry_tag}'
            sess, lerr = bk_login(box['address'], pwd)
            if not sess:
                st['status'] = f'login: {lerr}, retry 15s{retry_tag}'
                _sleep_stoppable(15, job)
                continue

            STATE['session'] = sess
            save_state()
            r = bk_sess_call(sess, '/auth/verification', method='POST',
                             body={}, use_csrf=True)
            sent = '_err' not in r

            st['status'] = f'tunggu email verif...{retry_tag}'
            vtok = None
            if sent:
                if provider == 'generator.email':
                    html = gen_poll(box['address'])
                    if html:
                        mt2 = re.search(r'verify=([A-Za-z0-9_\-\.]{20,})', html)
                        if mt2:
                            vtok = mt2.group(1)
                elif provider == 'tempmail.plus':
                    frm, subj, body = tmpplus_poll(box['address'])
                    if body:
                        mt2 = re.search(r'verify=([A-Za-z0-9_\-\.]{20,})', body)
                        if mt2:
                            vtok = mt2.group(1)
                else:
                    vtok = mail_wait_verification(box)
            verified = False
            if vtok:
                verified, _ = bk_verify_email(vtok)
            if not verified:
                # ga dapat token verif — coba lagi dari login (email udah ada,
                # register ulang bakal ditolak; pakai resend verif)
                st['status'] = f'verif gagal, resend + tunggu ulang...{retry_tag}'
                sess2, _ = bk_login(box['address'], pwd)
                if sess2:
                    STATE['session'] = sess2
                    save_state()
                    bk_sess_call(sess2, '/auth/verification', method='POST',
                                 body={}, use_csrf=True)
                    if provider == 'generator.email':
                        html = gen_poll(box['address'], tries=10)
                        if html:
                            mt3 = re.search(r'verify=([A-Za-z0-9_\-\.]{20,})', html)
                            if mt3:
                                vtok = mt3.group(1)
                    elif provider == 'tempmail.plus':
                        frm, subj, body = tmpplus_poll(box['address'], tries=10)
                        if body:
                            mt3 = re.search(r'verify=([A-Za-z0-9_\-\.]{20,})', body)
                            if mt3:
                                vtok = mt3.group(1)
                    else:
                        vtok = mail_wait_verification(box, tries=10, gap=5)
                    if vtok:
                        verified, _ = bk_verify_email(vtok)
                if not verified:
                    st['status'] = f'verif gagal total, retry akun baru 15s{retry_tag}'
                    _sleep_stoppable(15, job)
                    continue  # akun baru dari nol

            STATE['accounts'].append({
                'email': box['address'], 'mail_pwd': box['password'], 'password': pwd,
                'user_id': user.get('id'), 'verified': verified,
                'created_at': time.strftime('%Y-%m-%d %H:%M'), 'server': None,
            })
            save_state()
            job['ok'] += 1
            st['step'] = 'jadi'
            st['status'] = ('✅ verified' if verified else '⚠️') + retry_tag
            break  # sukses -> next item di queue
        else:
            # attempt habis -> REAL fail
            st['step'] = 'gagal'
            st['email'] = ''
            st['pwd'] = ''
            st['status'] = f'❌ nyerah setelah {MAX_ATTEMPTS} coba'
            job['fail'] += 1


def _sleep_stoppable(sec, job):
    """Tidur yang bisa dibatalin — pecah per 2s, cek flag stop."""
    for _ in range(0, int(sec), 2):
        if STOP_JOBS.get(job['id']):
            return False
        time.sleep(min(2, sec))
    return not STOP_JOBS.get(job['id'])


def acc_log_text(job):
    n_ok, n_fail = job['ok'], job['fail']
    stpj = sum(1 for w in range(1, job['threads'] + 1)
               if job['thread_state'][w]['step'] in ('selesai', 'gagal'))
    pending = job['total'] - n_ok - n_fail - stpj
    lines = [
        "🎧 <b>Buat Akun — live log</b>",
        f"Proxy : {'🏭 pool' if job['proxy_mode'] == 'pool' else '📤 upload'} "
        f"({len(job.get('proxies') or [])} available)",
        f"Target: {job['total']} akun · {job['threads']} thread",
        f"Progress: ✅ {n_ok} · ❌ {n_fail} · ⏳ {pending}",
        "",
    ]
    for wid in range(1, job['threads'] + 1):
        st = job['thread_state'][wid]
        icon = ACC_STEPS.get(st['step'], '•')
        em = st.get('email') or ''
        pw = st.get('pwd') or ''
        stat = st.get('status') or ''
        lines.append(f"{icon} T{wid} <code>{em}</code>|<code>{pw}</code>|{stat}")
    return '\n'.join(lines)


KB_JOB_STOP = [[{'text': '⏹ STOP worker', 'callback_data': 'job_stop'}]]


def acc_supervisor(job, chat_id, msg_id):
    last = ''
    while True:
        alive = any(job['thread_state'][w]['step'] not in ('selesai', 'gagal')
                    for w in range(1, job['threads'] + 1))
        stopped = STOP_JOBS.get(job['id'])
        if not alive or stopped:
            break
        txt = acc_log_text(job)
        if txt != last:
            last = txt
            edit(chat_id, msg_id, txt, KB_JOB_STOP)
        time.sleep(4)
    txt = acc_log_text(job) + ("\n\n<b>⛔ Di-stop.</b>" if stopped else "\n\n<b>🏁 Selesai.</b>")
    edit(chat_id, msg_id, txt, KB_AFTER_ACC)


# ---------------- AUTORETRY (server ladder per-akun) ----------------
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


def watch_log_text():
    w = STATE['watch']
    st = '✅ AKTIF' if w['enabled'] else '⛔ mati'
    spek = SPEK_LADDER[w.get('spek_idx', 0)]
    err = f"\nLast err : <code>{w['last_error']}</code>" if w.get('last_error') else ''
    return ("🔁 <b>Autoretry — live log</b>\n"
            f"Status : {st} · spek {spek['mem']}MB/{spek['cpu']}%/{spek['disk']}MB\n"
            f"Interval : {WATCH_INTERVAL}s · Attempts : {w['attempts']}{err}\n\n"
            + watch_accounts_text())


def watch_accounts_text():
    accs = STATE.get('accounts', [])
    spek = None
    if STATE.get('_spek_cache') and time.time() - STATE['_spek_cache'].get('t', 0) < 600:
        spek = STATE['_spek_cache'].get('v')
    lines = [f"Total Akun : {len(accs)}"]
    for i, a in enumerate(accs, 1):
        em = a.get('email', '')
        if a.get('server'):
            sv = a['server']
            data = f"{sv.get('name')} ({sv.get('status', '-')})"
        else:
            data = '⏳ nunggu'
        lines.append(f"Akun {i} : <code>{em}</code>|{spek or '-'}|{data}")
    return '\n'.join(lines)


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


def notify_server_dapat(res, spek):
    sv = res.get('name', '-')
    url = res.get('url') or 'https://botkeep.cloud/app'
    for uid in AUTH_USERS:
        send(uid, "🎉 <b>SERVER DAPAT!</b>\n\n"
                  f"Nama    : <b>{sv}</b>\n"
                  f"ID      : <code>{res.get('id')}</code>\n"
                  f"Spek    : {spek['mem']}MB / {spek['cpu']}% / {spek['disk']}MB\n"
                  f"Runtime : python 3.12\n"
                  f"Panel   : {url}")


def watch_attempt(chat_id=None, msg_id=None):
    """Satu siklus: cek tiap akun, kalau belum ada server, coba create."""
    w = STATE['watch']
    w['attempts'] += 1
    w['last_check'] = time.time()
    accs = STATE.get('accounts', [])
    got_any = False
    err = None
    for a in accs:
        if a.get('server'):
            continue
        # pindah sesi BK ke akun ini
        sess, lerr = bk_login(a['email'], a['password'])
        if not sess:
            err = f"{a['email']}: {lerr}"
            continue
        STATE['session'] = sess
        res, spek, e = try_create_server()
        if res:
            assign_server(a, res, spek)
            got_any = True
            w['last_error'] = None
            w['spek_idx'] = 0
            notify_server_dapat(res, spek)
            break   # 1 server/cycle cukup
        else:
            err = e
    w['last_error'] = err or w.get('last_error')
    if got_any:
        w['spek_idx'] = 0
    w['last_error'] = err if not got_any else None
    save_state()
    if chat_id and msg_id:
        edit(chat_id, msg_id, watch_log_text())


def watcher_loop():
    while True:
        w = STATE['watch']
        if w['enabled'] and STATE.get('session'):
            if time.time() - w['last_check'] >= WATCH_INTERVAL:
                watch_attempt()
        time.sleep(20)
# ---------------- views ----------------
def v_status():
    accs = STATE.get('accounts', [])
    if not accs:
        return "📭 <b>Belum ada akun</b>\nBikin dulu 👇", KB_MAIN
    # spek = plan limits dari dashboard (cache 5 menit biar klik ga nunggu HTTP)
    plan_spek = None
    if STATE.get('_spek_cache') and time.time() - STATE['_spek_cache'].get('t', 0) < 300:
        plan_spek = STATE['_spek_cache'].get('v')
    if plan_spek is None:
        d = bk('/panel/dashboard')
        if not d.get('_err'):
            a = d.get('account', {})
            lim = a.get('planLimits', {})
            plan_spek = (f"{lim.get('botSlots', 1)} slot · "
                         f"{lim.get('totalMemoryMb', '?')}MB · "
                         f"{lim.get('totalCpuPercent', '?')}% · "
                         f"{lim.get('totalStorageMb', '?')}MB")
            STATE['_spek_cache'] = {'t': time.time(), 'v': plan_spek}
            save_state()
        else:
            plan_spek = '?'
    out = f"📊 <b>Akun Saya</b>\n\nTotal : <b>{len(accs)}</b>\n\n"
    for i, a in enumerate(accs, 1):
        em = a.get('email', '')
        server = a.get('server')
        if server:
            data = f"<b>{server.get('name')}</b> ({server.get('status', '-')})"
        else:
            data = '⏳ nunggu'
        vmark = '✅' if a.get('verified') else '❌'
        out += f"Akun {i} : <code>{em}</code>|{plan_spek}|{data} {vmark}\n"
    out += f"\n🖥 Server aktif: <b>{sum(1 for a in accs if a.get('server'))}</b>/{len(accs)}"
    return out, KB_BACK


def v_servers():
    accs = [a for a in STATE.get('accounts', []) if a.get('server')]
    if not accs:
        return ("📭 <b>Belum ada server</b>\n\n"
                "Aktifin autoretry biar bot grepe kapasitas otomatis."), KB_WATCH
    out = f"🖥 <b>Server kamu ({len(accs)})</b>\n"
    for i, a in enumerate(accs, 1):
        sv = a['server']
        out += ("\n<blockquote>"
                f"Akun {i} : <code>{a['email']}</code>\n"
                f"Nama    : <b>{sv.get('name')}</b>\n"
                f"ID      : <code>{sv.get('id')}</code>\n"
                f"Spek    : {sv.get('spek')}\n"
                f"Runtime : {sv.get('runtime')}\n"
                f"Status  : {sv.get('status')}</blockquote>")
    return out, KB_WATCH


def v_watch():
    return watch_log_text(), KB_WATCH


def v_proxy():
    mode = STATE.get('proxy_mode', 'pool')
    n = (len(STATE.get('upload_proxies', [])) if mode == 'upload'
         else len(pool_proxies()))
    icon = '📤 Upload' if mode == 'upload' else '🏭 Pool'
    return (f"🏭 <b>Mode Proxy: {icon}</b> — {n} proxy siap\n\n"
            "<blockquote>"
            "Pool   : live dari proxysystem, ga disimpan\n"
            "Upload : kirim .txt host:port per baris, RAM only</blockquote>"), KB_PROXY


# ---------------- handler (async per update, biar ga lambat) ----------------
def handle_update(u):
    if 'callback_query' in u:
        cq = u['callback_query']
        uid = cq['from']['id']
        cbid = cq['id']
        data = cq.get('data', '')
        m = cq.get('message') or {}
        chat = m.get('chat', {}).get('id', uid)
        mid = m.get('message_id')
        if uid not in AUTH_USERS:
            ack(cbid, '⛔ Bukan owner.', True); return

        if data == 'menu':
            ack(cbid)
            edit(chat, mid, "👑 <b>Botkeep Manager</b>\n\nPilih menu 👇", KB_MAIN)

        elif data == 'acc_start':
            ack(cbid)
            DRAFT[chat] = {}
            edit(chat, mid, "👤 <b>Buat Akun — Step 1/3</b>\n\n"
                            "🏭 <b>Pilih sumber proxy:</b>\n"
                            "• Pool — live dari proxysystem\n"
                            "• Upload — kirim .txt, RAM only", KB_PROXY)

        elif data == 'acc_proxy_pool':
            n = len(pool_proxies())
            ack(cbid, f"🏭 Pool: {n} proxy")
            DRAFT.setdefault(chat, {})['proxy_mode'] = 'pool'
            edit(chat, mid, f"👤 <b>Buat Akun — Step 2/3</b>\n\n"
                            f"🏭 Proxy: pool ({n} available)\n\n"
                            "<b>Pilih jumlah akun:</b>", KB_COUNT)

        elif data == 'acc_proxy_upload':
            ack(cbid, '📤 Kirim file .txt proxy')
            DRAFT.setdefault(chat, {})['proxy_mode'] = 'upload'
            PENDING_UPLOAD[uid] = {'chat': chat, 'flow': 'acc'}
            send(chat, "📤 <b>Kirim file .txt proxy sekarang</b>\n\n"
                       "Format: <code>host:port</code> per baris. RAM only.")

        elif data.startswith('acc_n_'):
            n = int(data.split('_')[-1])
            DRAFT.setdefault(chat, {})['count'] = n
            ack(cbid, f'{n} akun dipilih')
            edit(chat, mid, "👤 <b>Buat Akun — Step 3/3</b>\n\n"
                            f"<b>{n} akun</b>. Pilih thread (max {MAX_THREADS}):",
                 KB_THREADS)

        elif data.startswith('acc_t_'):
            d = DRAFT.get(chat, {})
            threads = min(int(data.split('_')[-1]), MAX_THREADS)
            count = d.get('count', 1)
            pm = d.get('proxy_mode', 'pool')
            ack(cbid, f'🚀 {count} akun × {threads} thread. Gas!')
            proxies = pool_proxies() if pm == 'pool' else list(STATE.get('upload_proxies', []))
            eff = min(threads, count)
            job = {'id': f"{chat}:{mid}", 'proxy_mode': pm, 'proxies': proxies,
                   'total': count, 'threads': eff, 'ok': 0, 'fail': 0,
                   'queue': queue.Queue(), 'stopped': False,
                   'thread_state': {w: {'step': 'idle', 'email': '', 'pwd': '',
                                        'status': '', 'idx': None}
                                    for w in range(1, eff + 1)}}
            for i in range(count):
                job['queue'].put(i)
            JOBS[chat] = job
            mid2 = edit(chat, mid, acc_log_text(job))
            if mid2.get('ok'):
                mid = mid2['result']['message_id']
            for wid in range(1, eff + 1):
                threading.Thread(target=acc_worker, args=(wid, job), daemon=True).start()
            threading.Thread(target=acc_supervisor, args=(job, chat, mid), daemon=True).start()

        elif data == 'job_stop':
            # cari job di chat ini -> set stop flag (JOBS di-key chat)
            jlast = None
            for jkey, jb in list(JOBS.items()):
                if jkey == chat or str(jkey).startswith(f"{chat}:"):
                    STOP_JOBS[jb['id']] = True
                    jlast = jb
            if jlast:
                ack(cbid, '⛔ Worker stop sinyal dikirim')
                edit(chat, mid, acc_log_text(jlast) + "\n\n<b>⛔ Di-stop.</b>", KB_AFTER_ACC)
            else:
                ack(cbid, '⚠️ Ga ada job jalan', True)

        elif data == 'status':
            ack(cbid)
            txt, kb = v_status(); edit(chat, mid, txt, kb)

        elif data == 'servers':
            ack(cbid)
            txt, kb = v_servers(); edit(chat, mid, txt, kb)

        elif data == 'watch_menu':
            ack(cbid)
            txt, kb = v_watch(); edit(chat, mid, txt, kb)

        elif data == 'watch_on':
            if not STATE.get('session'):
                ack(cbid, '⚠️ Buat akun dulu!', True); return
            STATE['watch']['enabled'] = True
            STATE['watch']['last_check'] = 0
            STATE['watch']['last_error'] = None
            save_state()
            ack(cbid, '✅ Autoretry aktif!')
            edit(chat, mid, watch_log_text(), KB_WATCH)

        elif data == 'watch_off':
            STATE['watch']['enabled'] = False
            save_state()
            ack(cbid, '⏹ Autoretry mati')
            edit(chat, mid, watch_log_text(), KB_WATCH)

        elif data == 'watch_check':
            ack(cbid, '🔍 Cek kapasitas...')
            threading.Thread(target=watch_attempt, args=(chat, mid), daemon=True).start()

    elif 'message' in u:
        m = u['message']
        chat, uid = m['chat']['id'], m['from']['id']
        if uid not in AUTH_USERS:
            return
        doc = m.get('document')
        pend = PENDING_UPLOAD.get(uid)
        if doc and pend:
            f = tg('getFile', {'file_id': doc['file_id']})
            if f.get('ok'):
                fp = f['result']['file_path']
                content = urllib.request.urlopen(
                    f'https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}',
                    timeout=30).read().decode()
                proxies = [l.strip() for l in content.splitlines()
                           if l.strip() and ':' in l]
                STATE['proxy_mode'] = 'upload'
                STATE['upload_proxies'] = proxies
                save_state()
                PENDING_UPLOAD.pop(uid, None)
                send(chat, f"📤 <b>{len(proxies)} proxy dimuat</b> (RAM only)")
                DRAFT.setdefault(chat, {})['proxy_mode'] = 'upload'
                send(chat, "👤 <b>Buat Akun — Step 2/3</b>\n\n"
                           f"📤 Proxy: upload ({len(proxies)} dimuat)\n\n"
                           "<b>Pilih jumlah akun:</b>", KB_COUNT)
            return
        text = (m.get('text') or '').strip()
        if text.startswith('/stop'):
            jlast = None
            for jkey, jb in list(JOBS.items()):
                if jkey == chat or str(jkey).startswith(f"{chat}:"):
                    STOP_JOBS[jb['id']] = True
                    jlast = jb
            if jlast:
                send(chat, "⛔ <b>Worker di-stop paksa.</b> Akun yang udah jadi tetep ke-save.",
                     KB_MAIN)
            else:
                send(chat, "📭 Ga ada job yang lagi jalan.", KB_MAIN)
        elif text.startswith('/start'):
            send(chat, "👑 <b>Botkeep Manager</b>\n\nPilih menu 👇", KB_MAIN)


def main():
    threading.Thread(target=watcher_loop, daemon=True).start()
    offset = 0
    # poll pake koneksi TERPISAH — klik/update ga pernah ngantri di balik poll wait
    poll_conn = http.client.HTTPSConnection('api.telegram.org', context=_ssl_ctx,
                                            timeout=10)
    inited = False
    while True:
        try:
            req_body = json.dumps({
                'timeout': 3, 'limit': 10,
                'offset': offset if inited else -1}).encode()
            poll_conn.request('POST', f'/bot{BOT_TOKEN}/getUpdates', body=req_body,
                              headers={'Content-Type': 'application/json'})
            r = json.loads(poll_conn.getresponse().read())
            if not inited:
                ups = (r or {}).get('result', [])
                if ups:
                    offset = ups[-1]['update_id']  # skip backlog lama
                inited = True
                continue
            for u in (r or {}).get('result', []):
                offset = u['update_id'] + 1
                threading.Thread(target=handle_update, args=(u,), daemon=True).start()
        except Exception as e:
            print('poll err:', e)
            try:
                poll_conn.close()
            except Exception:
                pass
            poll_conn = http.client.HTTPSConnection('api.telegram.org',
                                                    context=_ssl_ctx, timeout=10)
            time.sleep(2)


# ---------------- CLI mode (tanpa Telegram) ----------------
def cli_run():
    """Jalan tanpa Telegram: python app.py --cli [jumlah_akun] [--threads N] [--watch]"""
    import argparse
    ap = argparse.ArgumentParser(description='Botkeep account factory + autoretry (CLI)')
    ap.add_argument('--cli', action='store_true', help='mode CLI')
    ap.add_argument('count', nargs='?', type=int, default=1, help='jumlah akun (default 1)')
    ap.add_argument('--threads', type=int, default=2, help='thread (default 2, max sama solver)')
    ap.add_argument('--watch', action='store_true', help='aktifin autoretry server')
    args = ap.parse_args()

    import queue as _q
    eff = max(1, min(args.threads, MAX_THREADS, args.count))
    job = {'id': 'cli', 'proxy_mode': 'pool', 'proxies': pool_proxies(),
           'total': args.count, 'threads': eff, 'ok': 0, 'fail': 0,
           'queue': _q.Queue(),
           'thread_state': {w: {'step': 'idle', 'email': '', 'pwd': '', 'status': '', 'idx': None}
                            for w in range(1, eff + 1)}}
    for i in range(args.count):
        job['queue'].put(i)

    def printer():
        last = ''
        while True:
            alive = any(job['thread_state'][w]['step'] not in ('selesai', 'gagal')
                        for w in range(1, eff + 1))
            txt = acc_log_text(job).replace('<b>', '').replace('</b>', '').replace('<code>', '')
            if txt != last:
                last = txt
                print('\n' + txt)
            if not alive:
                break
            time.sleep(3)

    threads = [threading.Thread(target=acc_worker, args=(w, job), daemon=True)
               for w in range(1, eff + 1)]
    pt = threading.Thread(target=printer, daemon=True)
    pt.start()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print('\n=== HASIL ===')
    for a in STATE.get('accounts', []):
        print(f"{a['email']}|{a['password']}|{'verified' if a.get('verified') else 'belum'}")
    if args.watch:
        print('\n🔁 Autoretry aktif (Ctrl+C stop)...')
        STATE['watch']['enabled'] = True
        save_state()
        watcher_loop()


if __name__ == '__main__':
    import sys
    if '--cli' in sys.argv:
        cli_run()
    elif not BOT_TOKEN or not AUTH_USERS:
        print('BOT_TOKEN / AUTH_USERS belum diset di .env. Pakai --cli buat mode tanpa Telegram.')
        sys.exit(1)
    else:
        main()
