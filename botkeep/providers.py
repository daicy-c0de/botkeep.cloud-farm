"""Email provider chain: generator.email -> mail.tm -> tempmail.plus + proxy pool."""
import json, re, secrets, string, time
import urllib.request, urllib.error
from .config import UA

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



# ---------------- proxy ----------------
def pool_proxies():
    try:
        data = json.load(open(os.environ.get('POOL_FILE', 'proxies.json')))
        return [p['addr'] for p in data if p.get('https')]
    except Exception:
        return []


