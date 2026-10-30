"""Telegram API — keep-alive, 1 koneksi TLS dipake ulang."""
# ---------------- telegram (keep-alive, 1 koneksi TLS dipake ulang) ----------------
import json, threading
import http.client, ssl as _ssl
from .config import BOT_TOKEN
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


