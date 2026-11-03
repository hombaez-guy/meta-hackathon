"""Telegram: the report lands on the admin's phone, with Apply and Rerun under each proposed fix.

No public URL. Foreman long-polls Telegram's getUpdates from a background thread, so it works
from inside the container with nothing exposed. Stdlib only.

Setup:
  1. Message @BotFather in Telegram, /newbot, copy the token into .env as TELEGRAM_BOT_TOKEN.
  2. Start Foreman, open the bot in Telegram, send /start. Foreman records the chat id.
  3. Every review with findings is sent to that chat. TELEGRAM_NOTIFY=all sends healthy ones too.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from config.settings import settings
from core.schemas import FixProposal, Report

log = logging.getLogger("foreman")

API = "https://api.telegram.org/bot{token}/{method}"
STATE = settings.data_dir / "telegram.json"  # {"chat_id": ..., "offset": ...}
_stop = threading.Event()
_thread: threading.Thread | None = None

ICON = {"healthy": "\U0001F7E2", "warning": "\U0001F7E1", "critical": "\U0001F534"}


# ---------- Telegram API ----------


def _call(method: str, **params) -> dict:
    """One Telegram Bot API call. Returns the 'result' field, or {} on failure (never raises)."""
    if not settings.telegram_bot_token:
        return {}
    url = API.format(token=settings.telegram_bot_token, method=method)
    body = json.dumps(params).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=params.get("timeout", 10) + 10) as r:
            data = json.loads(r.read())
            return data.get("result") or {}
    except urllib.error.HTTPError as e:
        log.warning("telegram %s: HTTP %s %s", method, e.code, e.read()[:200])
    except Exception as e:  # noqa: BLE001
        log.warning("telegram %s: %s", method, e)
    return {}


def _state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def _save_state(**kw) -> None:
    s = _state()
    s.update(kw)
    STATE.write_text(json.dumps(s))


def chat_id() -> str | None:
    return settings.telegram_chat_id or _state().get("chat_id")


def enabled() -> bool:
    return bool(settings.telegram_bot_token)


# ---------- Outbound: the report ----------


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fix_buttons(fix: FixProposal) -> list[list[dict]]:
    if fix.state != "proposed":
        return []
    return [[
        {"text": "Apply", "callback_data": f"apply:{fix.fix_id}"},
        {"text": "Rerun", "callback_data": f"rerun:{fix.fix_id}"},
    ]]


def send_report(report: Report) -> None:
    """Headline message, then one message per proposed fix with its buttons."""
    if not enabled():
        return
    cid = chat_id()
    if not cid:
        log.info("telegram: no chat yet. Send /start to the bot.")
        return
    v = report.review
    if settings.telegram_notify != "all" and not v.findings:
        return

    text = (
        f"{ICON.get(v.overall_status, '')} <b>{_esc(v.overall_status.upper())}</b> on <code>{_esc(report.server)}</code>\n"
        f"<b>{_esc(v.headline)}</b>\n{_esc(v.summary)}\n\n"
        f"CPU {report.chip.cpu_percent:.0f}% · Mem {report.chip.memory_percent:.0f}% · Disk {report.chip.disk_percent:.0f}%"
        f" · {len(v.findings)} finding{'s' if len(v.findings) != 1 else ''}\n<i>{_esc(report.report_id)}</i>"
    )
    _call("sendMessage", chat_id=cid, text=text, parse_mode="HTML")

    for f in v.findings:
        if not f.fix:
            continue
        steps = "\n".join(f"{i + 1}. <code>{_esc(s.command)}</code>" for i, s in enumerate(f.fix.steps[:5]))
        msg = (
            f"<b>{_esc(f.title)}</b>  [{_esc(f.severity)}]\n{_esc(f.evidence)}\n\n"
            f"<b>Proposed fix:</b> {_esc(f.fix.title)}  (risk {_esc(f.fix.risk)})\n{_esc(f.fix.explanation)}\n{steps}"
        )
        if f.fix.rollback:
            msg += f"\n<i>Rollback: {_esc(f.fix.rollback)}</i>"
        _call("sendMessage", chat_id=cid, text=msg, parse_mode="HTML",
              reply_markup={"inline_keyboard": _fix_buttons(f.fix)})


def send_text(text: str) -> None:
    cid = chat_id()
    if enabled() and cid:
        _call("sendMessage", chat_id=cid, text=text, parse_mode="HTML")


# ---------- Inbound: /start and button taps ----------


def _handle_callback(cb: dict) -> None:
    from core.pipeline import apply_fix, rerun_fix

    data = cb.get("data", "")
    action, _, fix_id = data.partition(":")
    msg = cb.get("message") or {}
    cid, mid = msg.get("chat", {}).get("id"), msg.get("message_id")
    _call("answerCallbackQuery", callback_query_id=cb["id"], text=f"{action.title()}ing…")
    try:
        if action == "apply":
            fix = apply_fix(fix_id)
            out = (fix.result or "")[-600:]
            text = (f"{'✅' if fix.state == 'applied' else '❌'} <b>{_esc(fix.state.upper())}</b>: {_esc(fix.title)}\n"
                    f"<pre>{_esc(out) or 'no output'}</pre>")
            _call("editMessageReplyMarkup", chat_id=cid, message_id=mid, reply_markup={"inline_keyboard": []})
            _call("sendMessage", chat_id=cid, text=text, parse_mode="HTML")
        elif action == "rerun":
            new = asyncio.run(rerun_fix(fix_id))
            steps = "\n".join(f"{i + 1}. <code>{_esc(s.command)}</code>" for i, s in enumerate(new.steps[:5]))
            text = (f"\U0001F501 <b>New proposal (attempt {new.attempt}):</b> {_esc(new.title)}  (risk {_esc(new.risk)})\n"
                    f"{_esc(new.explanation)}\n{steps}")
            if new.conflicts_with_previous:
                text += f"\n<i>Vs previous: {_esc(new.conflicts_with_previous)}</i>"
            _call("editMessageReplyMarkup", chat_id=cid, message_id=mid, reply_markup={"inline_keyboard": []})
            _call("sendMessage", chat_id=cid, text=text, parse_mode="HTML",
                  reply_markup={"inline_keyboard": _fix_buttons(new)})
        else:
            _call("sendMessage", chat_id=cid, text=f"Unknown action: {_esc(data)}")
    except KeyError:
        _call("sendMessage", chat_id=cid, text=f"Fix <code>{_esc(fix_id)}</code> not found.", parse_mode="HTML")
    except Exception as e:  # noqa: BLE001
        log.error("telegram %s %s failed: %s", action, fix_id, e)
        _call("sendMessage", chat_id=cid, text=f"❌ {_esc(action)} failed: {_esc(str(e)[:300])}", parse_mode="HTML")


def _handle_message(m: dict) -> None:
    from core.store import Store

    cid = str(m.get("chat", {}).get("id"))
    text = (m.get("text") or "").strip()
    if text.startswith("/start"):
        _save_state(chat_id=cid)
        name = m.get("from", {}).get("first_name", "admin")
        _call("sendMessage", chat_id=cid, parse_mode="HTML",
              text=f"Foreman connected, {_esc(name)}. Reports for <code>{_esc(settings.server_name)}</code> will land here. "
                   f"Send /status for the latest, /review to run one now.")
        log.info("telegram: chat %s registered", cid)
    elif text.startswith("/status"):
        r = Store().latest_report()
        if not r:
            _call("sendMessage", chat_id=cid, text="No reports yet.")
        else:
            v = r.review
            _call("sendMessage", chat_id=cid, parse_mode="HTML",
                  text=f"{ICON.get(v.overall_status, '')} <b>{_esc(v.overall_status.upper())}</b> · {_esc(r.created_at[:16].replace('T', ' '))} UTC\n"
                       f"{_esc(v.headline)}\n{len(v.findings)} findings, {len(r.open_fixes)} to approve")
    elif text.startswith("/review"):
        from core.pipeline import run_review

        _call("sendMessage", chat_id=cid, text="Reviewing…")
        try:
            r = asyncio.run(run_review(server=settings.server_name))
            if not r.review.findings or settings.telegram_notify == "all":
                send_text(f"{ICON.get(r.review.overall_status, '')} {_esc(r.review.headline)}")
        except Exception as e:  # noqa: BLE001
            _call("sendMessage", chat_id=cid, text=f"❌ review failed: {_esc(str(e)[:300])}", parse_mode="HTML")
    elif text.startswith("/"):
        _call("sendMessage", chat_id=cid, text="Commands: /status, /review. Fixes come with Apply and Rerun buttons.")


def _loop() -> None:
    offset = _state().get("offset", 0)
    log.info("telegram: polling for /start and button taps")
    while not _stop.is_set():
        updates = _call("getUpdates", offset=offset, timeout=25, allowed_updates=["message", "callback_query"])
        if not isinstance(updates, list):
            time.sleep(3)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            try:
                if "callback_query" in u:
                    _handle_callback(u["callback_query"])
                elif "message" in u:
                    _handle_message(u["message"])
            except Exception as e:  # noqa: BLE001
                log.error("telegram update failed: %s", e)
            _save_state(offset=offset)


def start() -> None:
    """Called from the API lifespan. No-op without a token."""
    global _thread
    if not enabled() or _thread:
        return
    me = _call("getMe")
    if not me:
        log.warning("telegram: token set but getMe failed; check TELEGRAM_BOT_TOKEN")
        return
    log.info("telegram: bot @%s ready%s", me.get("username"), "" if chat_id() else " (send /start to the bot)")
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="telegram", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
