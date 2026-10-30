"""Worker, supervisor, autoretry watcher, views (teks + keyboard hasil view)."""
import re, secrets, time, threading, queue
import urllib.parse
from .config import (STATE, save_state, MAX_THREADS, MAX_ATTEMPTS, SOLVE_BACKOFF,
                     WATCH_INTERVAL, SPEK_LADDER, AUTH_USERS)
from .telegram import send, edit
from .providers import (gen_create, gen_poll, mail_create, mail_wait_verification,
                        tmpplus_create, tmpplus_poll, pool_proxies)
from .solver import solve_turnstile
from .api import (bk_login, bk_sess_call, bk_register, bk_verify_email, bk,
                  try_create_server, assign_server, build_payload)

# ---------------- BUAT AKUN ----------------
ACC_STEPS = {
    'idle': '💤', 'email': '📧', 'solve': '🧩', 'register': '📝',
    'verif': '📨', 'gagal': '❌', 'jadi': '✅', 'selesai': '🏁',
}


STOP_JOBS = {}   # job_id -> True (sinyal stop buat worker + supervisor)


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


