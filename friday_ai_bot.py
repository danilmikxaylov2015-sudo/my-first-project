# FRIDAY AI Telegram Bot
# Single file template for Bothost.ru
# Replace tokens before running.

import sys, subprocess, importlib, json, os

for m,p in {"telegram":"python-telegram-bot","openai":"openai","requests":"requests"}.items():
    try:
        importlib.import_module(m)
    except ImportError:
        subprocess.check_call([sys.executable,"-m","pip","install",p])

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from openai import OpenAI
import requests

TG_TOKEN = "8975361055:AAET6brDJIAonm58z-2CNCHG-1WEMuC0Rmc"
API_KEY = "tf_live_p-5EIvVNV11q1yfBPS5M3wUSNlkiOFi_vfL5-eFNnFU"
ADMIN_ID = 8343382233

BASE_URL = "https://tokengate-cqt9ivzs.manus.space/v1"
DEFAULT_MODEL = "claude-fable-5"

SYSTEM = """
Ты FRIDAY AI.
Ты умный ассистент.
Помогаешь с кодом, анализом и творческими задачами.
"""

CHARACTERS = {
    "friday":"Ты FRIDAY, высокоинтеллектуальный помощник.",
    "developer":"Ты Senior разработчик программного обеспечения.",
    "wizard":"Ты мудрый маг из фэнтези мира."
}

DB="users.json"

client=OpenAI(api_key=API_KEY,base_url=BASE_URL)

def load():
    if not os.path.exists(DB):
        return {}
    return json.load(open(DB,"r",encoding="utf8"))

def save(x):
    json.dump(x,open(DB,"w",encoding="utf8"),ensure_ascii=False,indent=2)

def user(uid):
    db=load()
    if str(uid) not in db:
        db[str(uid)]={"credits":3,"requests":6,"model":DEFAULT_MODEL,"role":"friday","prompt":""}
        save(db)
    return db

async def start(u,c):
    user(u.effective_user.id)
    await u.message.reply_text("🤖 FRIDAY AI\n3 кредита = 6 запросов\n/menu")

async def menu(u,c):
    kb=[
        [InlineKeyboardButton("🎭 Роли",callback_data="roles")],
        [InlineKeyboardButton("🤖 Модели",callback_data="models")],
        [InlineKeyboardButton("💎 Баланс",callback_data="balance")]
    ]
    await u.message.reply_text("Меню",reply_markup=InlineKeyboardMarkup(kb))

async def profile(u,c):
    x=user(u.effective_user.id)[str(u.effective_user.id)]
    await u.message.reply_text(str(x))

async def button(u,c):
    q=u.callback_query
    await q.answer()
    if q.data=="roles":
        await q.edit_message_text("\n".join(CHARACTERS.keys()))
    elif q.data=="models":
        await q.edit_message_text("Проверка моделей: /check")
    else:
        await q.edit_message_text("Пополнение через Telegram Stars")

async def check(u,c):
    r=requests.get(BASE_URL+"/models",headers={"Authorization":"Bearer "+API_KEY})
    await u.message.reply_text(str([x["id"] for x in r.json().get("data",[])]))

async def chat(u,c):
    uid=u.effective_user.id
    db=user(uid)
    x=db[str(uid)]
    if uid!=ADMIN_ID and x["requests"]<=0:
        await u.message.reply_text("Нет запросов")
        return
    r=client.chat.completions.create(
        model=x["model"],
        messages=[
            {"role":"system","content":SYSTEM+"\n"+CHARACTERS[x["role"]]+"\n"+x["prompt"]},
            {"role":"user","content":u.message.text}
        ])
    if uid!=ADMIN_ID:
        x["requests"]-=1
        save(db)
    await u.message.reply_text(r.choices[0].message.content[:4000])

app=Application.builder().token(TG_TOKEN).build()
app.add_handler(CommandHandler("start",start))
app.add_handler(CommandHandler("menu",menu))
app.add_handler(CommandHandler("profile",profile))
app.add_handler(CommandHandler("check",check))
app.add_handler(CallbackQueryHandler(button))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,chat))

print("FRIDAY ONLINE")
app.run_polling()
