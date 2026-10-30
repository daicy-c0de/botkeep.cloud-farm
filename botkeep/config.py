"""Konstanta, env, state — sumber tunggal buat semua module."""
import os, json

def _load_env(path='.env'):
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

STATE_DIR = os.environ.get('STATE_DIR', '.')
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
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
