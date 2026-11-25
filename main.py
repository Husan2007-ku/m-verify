"""
Muxlisa — yakuniy sahifa 2-savol tasdiqlash serveri.
Frontend (artifact) oqimi:
  POST /verify  {session, name, answer}  -> botdan muallifga "Tasdiqlash/Rad etish" tugmasi yuboriladi
  GET  /status?session=...               -> {"status": "pending|approved|rejected"}
Muallif Telegramda tugmani bossa, holat yangilanadi (webhook orqali).
Maxfiy kalitlar faqat Render env'da: BOT_TOKEN, AUTHOR_CHAT_ID.
"""
import os
import time
import asyncio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
AUTHOR_CHAT_ID = os.environ.get("AUTHOR_CHAT_ID", "").strip()
# Darvozabon: shu username'larga (vergul bilan) sayt havolasi yuboriladi
_allowed_raw = os.environ.get("ALLOWED_USERNAMES", "") or os.environ.get("ALLOWED_USERNAME", "")
ALLOWED_USERNAMES = {u.strip().lstrip("@").lower() for u in _allowed_raw.split(",") if u.strip()}
SITE_URL = os.environ.get("SITE_URL", "").strip()
# Render ishga tushganda tashqi URL'ni o'zi beradi
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Eski yozuvlarni tozalab turish (soat)
TTL_SECONDS = 60 * 60

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # artifact origin dinamik; maxfiy ma'lumot qaytmaydi
    allow_methods=["*"],
    allow_headers=["*"],
)

# session -> {"status", "answer", "name", "ts"}  (xotirada; qayta deploy'da tozalanadi)
STORE: dict = {}


def _gc():
    now = time.time()
    for k in [k for k, v in STORE.items() if now - v.get("ts", now) > TTL_SECONDS]:
        STORE.pop(k, None)


async def tg_call(method: str, payload: dict) -> dict:
    if not BOT_TOKEN:
        return {}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{API}/{method}", json=payload)
            return r.json()
    except Exception:
        return {}


@app.on_event("startup")
async def on_startup():
    # Webhook'ni Render URL'iga o'rnatamiz
    if BOT_TOKEN and BASE_URL:
        await tg_call("setWebhook", {
            "url": f"{BASE_URL}/tg/webhook",
            "allowed_updates": ["callback_query", "message"],
        })


@app.get("/")
async def root():
    return {"ok": True, "configured": bool(BOT_TOKEN and AUTHOR_CHAT_ID)}


@app.post("/verify")
async def verify(req: Request):
    _gc()
    try:
        data = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "bad json"}, status_code=400)
    session = str(data.get("session", "")).strip()
    answer = str(data.get("answer", "")).strip()
    name = str(data.get("name", "")).strip()
    if not session:
        return JSONResponse({"ok": False, "error": "no session"}, status_code=400)
    STORE[session] = {"status": "pending", "answer": answer, "name": name, "ts": time.time()}

    if BOT_TOKEN and AUTHOR_CHAT_ID:
        text = (
            "🔐 Tasdiqlash so'rovi\n\n"
            f"{name or 'Tashrifchi'} «erkalab chaqirish» javobini yozdi:\n"
            f"«{answer}»\n\n"
            "Bu to'g'rimi?"
        )
        kb = {"inline_keyboard": [[
            {"text": "✅ Tasdiqlash", "callback_data": f"ok:{session}"},
            {"text": "❌ Rad etish", "callback_data": f"no:{session}"},
        ]]}
        await tg_call("sendMessage", {"chat_id": AUTHOR_CHAT_ID, "text": text, "reply_markup": kb})

    return {"ok": True}


@app.get("/status")
async def status(session: str = ""):
    s = STORE.get(session.strip())
    return {"status": s["status"] if s else "pending"}


@app.post("/notify")
async def notify(req: Request):
    """Sayt javoblarini (token sayt kodida bo'lmasligi uchun) server orqali Telegramga yuboradi."""
    try:
        data = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "bad json"}, status_code=400)
    text = str(data.get("text", ""))[:3800]
    if text and BOT_TOKEN and AUTHOR_CHAT_ID:
        await tg_call("sendMessage", {"chat_id": AUTHOR_CHAT_ID, "text": text})
    return {"ok": True}


@app.post("/tg/webhook")
async def webhook(req: Request):
    try:
        upd = await req.json()
    except Exception:
        return {"ok": True}

    # --- Oddiy xabar: darvozabon (sayt yetkazib berish) ---
    msg = upd.get("message") or upd.get("edited_message")
    if msg:
        frm = msg.get("from", {}) or {}
        chat = msg.get("chat", {}) or {}
        uname = (frm.get("username") or "").lstrip("@").lower()
        text = msg.get("text", "") or ""
        sender_id = str(chat.get("id") or frm.get("id") or "")
        is_author = bool(AUTHOR_CHAT_ID) and sender_id == str(AUTHOR_CHAT_ID)

        # 1) Ruxsat berilgan username(lar) -> sayt havolasi
        if uname and uname in ALLOWED_USERNAMES:
            greeting = (
                "Assalomu alaykum 🤍\n\n"
                "Senga atalgan bir maktub bor. Shoshilmay, boshidan oxirigacha o'qi:\n\n"
                f"{SITE_URL}"
            )
            await tg_call("sendMessage", {"chat_id": chat.get("id"), "text": greeting})
            if AUTHOR_CHAT_ID and not is_author:
                await tg_call("sendMessage", {"chat_id": AUTHOR_CHAT_ID,
                                              "text": f"✅ @{uname} botga yozdi — sayt havolasi yuborildi."})
            return {"ok": True}

        # 2) Muallif (sen) yozsa -> holat javobi
        if is_author:
            await tg_call("sendMessage", {"chat_id": chat.get("id"),
                "text": ("Bot ishlayapti ✅\n"
                         "Sayt havolasi faqat ruxsat etilgan akauntlarga yuboriladi.\n"
                         "Boshqa kim yozsa — shu yerda, senga ko'rinadi.")})
            return {"ok": True}

        # 3) Boshqa akauntlar -> tanishtirish so'rovi + muallifga yo'naltirish
        intro = (
            "Assalomu alaykum.\n"
            "Bu bot shaxsiy — hozircha siz uchun xizmat ko'rsatmaydi.\n"
            "Xizmatdan foydalanmoqchi bo'lsangiz, iltimos o'zingizni tanishtiring."
        )
        await tg_call("sendMessage", {"chat_id": chat.get("id"), "text": intro})
        if AUTHOR_CHAT_ID:
            who = ("@" + uname) if uname else (frm.get("first_name") or sender_id)
            await tg_call("sendMessage", {"chat_id": AUTHOR_CHAT_ID,
                                          "text": f"✉️ Botga notanish xabar — {who} (id {sender_id}):\n{text}"})
        return {"ok": True}

    cq = upd.get("callback_query")
    if not cq:
        return {"ok": True}

    data = cq.get("data", "") or ""
    cq_id = cq.get("id")
    msg = cq.get("message", {}) or {}
    chat_id = (msg.get("chat", {}) or {}).get("id")
    mid = msg.get("message_id")

    if ":" in data:
        action, session = data.split(":", 1)
        note = ""
        if session in STORE:
            if action == "ok":
                STORE[session]["status"] = "approved"
                note = "✅ Tasdiqlandi"
            elif action == "no":
                STORE[session]["status"] = "rejected"
                note = "❌ Rad etildi"
        else:
            note = "⏳ Bu so'rov eskirgan"
        await tg_call("answerCallbackQuery", {"callback_query_id": cq_id, "text": note or "OK"})
        if chat_id and mid and note:
            base = msg.get("text") or ""
            await tg_call("editMessageText", {
                "chat_id": chat_id, "message_id": mid, "text": base + "\n\n" + note,
            })

    return {"ok": True}
