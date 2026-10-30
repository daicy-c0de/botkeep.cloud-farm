"""Telegram bot: inline UI, callback handlers, loop utama."""
import json, time, threading, queue
import http.client
import urllib.request
from .config import (BOT_TOKEN, AUTH_USERS, STATE, save_state, MAX_THREADS,
                     PENDING_UPLOAD, JOBS, DRAFT)
from .telegram import tg, send, edit, ack, _ssl_ctx
from .providers import pool_proxies
from .workers import (acc_worker, acc_supervisor, acc_log_text, watch_attempt,
                      watcher_loop, watch_log_text, STOP_JOBS,
                      v_status, v_servers, v_watch, v_proxy, KB_MAIN, KB_PROXY,
                      KB_COUNT, KB_THREADS, KB_WATCH, KB_AFTER_ACC, KB_BACK)

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


