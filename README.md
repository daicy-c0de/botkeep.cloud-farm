# botkeep-manager

Auto-create botkeep.cloud accounts (temp mail + Turnstile solver + auto-verify email)
dan autoretry server creation sampai dapat. Full API, no browser自动化 di sisi client.

## Fitur
- 👤 Buat Akun: generator.email / mail.tm / tempmail.plus fallback chain + Turnstile solver + register + verify
- 🔁 Autoretry: cek kapasitas per akun tiap 5 menit, ladder spek otomatis (512→128 MB), notif Telegram saat dapat server
- 🏭 Proxy: pool JSON (live) atau upload .txt (RAM only)
- Live log format `Akun N : email|spek|status`, max thread = solver concurrency

## Jalan lokal TANPA Telegram (CLI)
```bash
cp .env.example .env   # isi SOLVER_KEY (dari solver lu sendiri), SITEKEY dll
python3 app.py --cli 3 --threads 2        # 3 akun, 2 thread
python3 app.py --cli 1 --watch            # 1 akun + autoretry server
```

## Jalan dengan Telegram bot
```bash
cp .env.example .env
# isi BOT_TOKEN (dari @BotFather bot lu sendiri), AUTH_USERS=<telegram_id lu>
python3 app.py
```

## Kebutuhan eksternal (jangan pake punya orang lain!)
- **Turnstile solver sendiri** — set `SOLVER_URL` + `SOLVER_KEY` env. Contoh solver open-source:
  - turnstile-solver (chromium) / camoufox-based solver — deploy sendiri di VPS/server lu
- **Proxy pool** — set `POOL_FILE` ke JSON `[{"addr": "host:port", "https": true}, ...]`
  atau upload `.txt` via bot. Direct IP besar kemungkinan di-WAF (HTTP 429).

## Environment variables
| Var | Wajib | Default | Keterangan |
|---|---|---|---|
| BOT_TOKEN | utk TG mode | - | token bot lu |
| AUTH_USERS | utk TG mode | - | Telegram user ID lu (koma utk multi) |
| SOLVER_KEY | ya | - | key solver lu |
| SOLVER_URL | tidak | http://localhost:8790/solve | endpoint solver |
| SOLVER_SITEKEY | tidak | (botkeep) | sitekey Turnstile |
| TARGET_URL | tidak | https://botkeep.cloud/register | halaman target solve |
| POOL_FILE | tidak | proxies.json | pool proxy JSON |
| STATE_FILE | tidak | ./state.json | persist akun + sesi |

## Disclaimer
Tools ini untuk edukasi / testing di environment yang lu punya izin.
Jangan dipake untuk abuse apapun. Resiko tanggung sendiri.
