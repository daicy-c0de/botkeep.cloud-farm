#!/usr/bin/env python3
"""@kuromistorex_bot — Botkeep Manager v5 (entry).

CLI  : python app.py --cli [jumlah_akun] [--threads N] [--watch]
TG   : python app.py  (butuh BOT_TOKEN + AUTH_USERS di .env)
"""
import sys
from botkeep.config import BOT_TOKEN, AUTH_USERS
from botkeep import cli, bot

if __name__ == '__main__':
    if '--cli' in sys.argv:
        cli.cli_run()
    elif not BOT_TOKEN or not AUTH_USERS:
        print('BOT_TOKEN / AUTH_USERS belum diset di .env. Pakai --cli buat mode tanpa Telegram.')
        sys.exit(1)
    else:
        bot.main()
