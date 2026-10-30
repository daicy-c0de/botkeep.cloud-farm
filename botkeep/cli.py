"""Mode CLI tanpa Telegram: python app.py --cli [jumlah] [--threads N] [--watch]."""
import time, threading
import queue as _q
from .config import STATE, save_state, MAX_THREADS
from .providers import pool_proxies
from .workers import acc_worker, acc_log_text, watcher_loop

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


