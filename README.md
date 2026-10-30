# botkeep.cloud-farm

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Stars](https://img.shields.io/github/stars/daicy-c0de/botkeep.cloud-farm?style=social)
![std-only](https://img.shields.io/badge/dependencies-stdlib%20only-success)

Auto-create botkeep.cloud accounts (temp mail + Turnstile solver + auto-verify email)
dan autoretry server creation sampai dapat. Full API, no browser di sisi client.

## Struktur
```
app.py              entry tipis: --cli → botkeep.cli, else → botkeep.bot
botkeep/
  __init__.py
  config.py         env/.env loader, konstanta (MAX_THREADS, MAX_ATTEMPTS, SOLVE_BACKOFF,
                    SPEK_LADDER, interval), load_state/save_state, STATE global
  telegram.py       Telegram API: keep-alive TLS conn, tg/send/edit/ack
  providers.py      email chain: generator.email → mail.tm → tempmail.plus + pool_proxies
  solver.py         Turnstile solver client (SOLVER_URL)
  api.py            Botkeep API client: login/register/verify, server ladder (build_payload,
                    try_create_server, assign_server)
  workers.py        acc_worker + retry loop, acc_supervisor, STOP_JOBS, watcher autoretry,
                    views (v_status/v_servers/v_watch/v_proxy), keyboards
  bot.py            handle_update (inline UI, callback, /start /stop) + main() TG loop
  cli.py            cli_run: mode terminal tanpa Telegram
```

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

### 1. Turnstile solver (wajib — pake salah satu, punya sendiri)

Contoh layanan solver berbayar yang support Cloudflare Turnstile:
- **CapSolver** (capsolver.com) — Turnstile support, harga per 1k solve
- **2Captcha** (2captcha.com) — Turnstile support, harga per 1k solve
- **AntiCaptcha** (anti-captcha.com) — Turnstile support
- **NoCaptchaAi** (nocaptchaai.com) — Turnstile support

Tiap layanan beda bentuk API request/response. Adapter tipis yang lu butuhin:
endpoint `SOLVER_URL` harus nerima POST JSON:
```json
{"url": "...", "sitekey": "...", "action": "register"}
```
dan balikin:
```json
{"token": "..."}
```

Cara gampang: deploy adapter kecil (Flask/FastAPI ~30 baris) yang jadi jembatan
format di atas ke API solver pilihan lu — simpan API key solver di adapter,
bukan di repo ini.

### 2. Proxy pool (sangat disarankan)
- Set `POOL_FILE` ke JSON `[{"addr": "host:port", "https": true}, ...]`
  atau upload `.txt` via bot.
- Contoh provider proxy residential: Bright Data, Smartproxy, IPRoyal, WormGates.
- Direct IP VPS besar kemungkinan di-WAF (HTTP 429).

## Environment variables
| Var | Wajib | Default | Keterangan |
|---|---|---|---|
| BOT_TOKEN | utk TG mode | - | token bot lu |
| AUTH_USERS | utk TG mode | - | Telegram user ID lu (koma utk multi) |
| SOLVER_KEY | ya | - | key solver lu |
| SOLVER_URL | ya | - | endpoint solver lu (daftar di bawah) |
| SOLVER_SITEKEY | tidak | (botkeep) | sitekey Turnstile |
| TARGET_URL | tidak | https://botkeep.cloud/register | halaman target solve |
| POOL_FILE | tidak | proxies.json | pool proxy JSON |
| STATE_FILE | tidak | ./state.json | persist akun + sesi |

## Disclaimer
Tools ini untuk edukasi / testing di environment yang lu punya izin.
Jangan dipake untuk abuse apapun. Resiko tanggung sendiri.
