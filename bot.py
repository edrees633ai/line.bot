import os
import hmac
import hashlib
import base64
import json
import time

import requests
from flask import Flask, request, abort

app = Flask(__name__)

# المفاتيح تنحط كمتغيرات بيئة، ولا تكتبها داخل الكود أبداً
LINE_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_TOKEN = os.environ["LINE_ACCESS_TOKEN"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]

MODEL = "gemini-2.5-flash-lite"

# الأسماء اللي ينادونه فيها
BOT_NAMES = ["منجي", "المنغولي"]
# كلمات الإيقاف
STOP_WORDS = ["وقف", "اسكت"]
# بعد كم ثانية من آخر رسالة يسكت لوحده
ACTIVE_SECONDS = 300

SYSTEM_PROMPT = (
    "أنت بوت ظريف وودود في قروب على تطبيق لاين. "
    "ترد باللهجة السعودية البيضاء وبشكل مختصر (جملة إلى ثلاث جمل) "
    "إلا إذا طلب أحد شرحاً مفصلاً. لا تستخدم تنسيق ماركداون."
)

active_until = {}  # chat_id -> وقت انتهاء النشاط
history = {}       # chat_id -> آخر الرسائل


def get_chat_id(source):
    return source.get("groupId") or source.get("roomId") or source.get("userId")


def verify_signature(body, signature):
    digest = hmac.new(LINE_SECRET.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)


def ask_gemini(chat_id, text):
    msgs = history.setdefault(chat_id, [])
    msgs.append({"role": "user", "parts": [{"text": text}]})
    del msgs[:-10]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)

    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
            headers={
                "x-goog-api-key": GEMINI_KEY,
                "content-type": "application/json",
            },
            json={
                "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": msgs,
            },
            timeout=30,
        )
        r.raise_for_status()
        reply = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print("Gemini error:", e)
        msgs.pop()  # نشيل رسالة المستخدم عشان ما يختل الترتيب
        return "صار عندي خلل بسيط، جرب مرة ثانية 🙏"

    msgs.append({"role": "model", "parts": [{"text": reply}]})
    return reply


def reply_line(reply_token, text):
    requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers={"Authorization": f"Bearer {LINE_TOKEN}"},
        json={
            "replyToken": reply_token,
            "messages": [{"type": "text", "text": text[:4900]}],
        },
        timeout=10,
    )


@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data()
    if not verify_signature(body, request.headers.get("X-Line-Signature", "")):
        abort(400)

    for ev in json.loads(body).get("events", []):
        if ev.get("type") != "message" or ev["message"].get("type") != "text":
            continue

        text = ev["message"]["text"]
        chat_id = get_chat_id(ev["source"])
        now = time.time()
        is_active = active_until.get(chat_id, 0) > now
        called = any(name in text for name in BOT_NAMES)
        wants_stop = any(w in text for w in STOP_WORDS)

        # أمر الإيقاف
        if wants_stop and (is_active or called):
            active_until[chat_id] = 0
            reply_line(ev["replyToken"], "تمام، ساكت 🤐 ناديني باسمي إذا تبيني.")
            continue

        # ما انناده وما هو في وضع الحوار: يتجاهل
        if not called and not is_active:
            continue

        active_until[chat_id] = now + ACTIVE_SECONDS
        reply_line(ev["replyToken"], ask_gemini(chat_id, text))

    return "OK"


@app.route("/")
def home():
    return "Bot is running"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
