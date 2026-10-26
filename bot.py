import os, sqlite3, asyncio, secrets
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton as B, CallbackQuery, Message

TOKEN = os.environ["BOT_TOKEN"]  # توکن فقط از متغیر محیطی خوانده می‌شود، نه از داخل کد
WALLET = os.getenv("WALLET", "UQBPLKmOF4Vnuxa71H2y0T1MFTZps4pk1cGmfeo1vQX7UTdO")
SUPPORT = "Reralo"
API_KEY = os.getenv("TONCENTER_KEY", "")
AMOUNTS = [0.5, 1, 2, 5, 10, 20, 50, 100]

db = sqlite3.connect("wallet.db", check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, balance INTEGER DEFAULT 0, joined TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, code TEXT UNIQUE, amount INTEGER,
  status TEXT DEFAULT 'pending', tx TEXT UNIQUE, created TEXT DEFAULT CURRENT_TIMESTAMP, paid TEXT);
""")
bot = Bot(TOKEN)
dp = Dispatcher()


def ton(n):
    return f"{n / 1e9:g}"


def menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [B(text="کیف پول", callback_data="w"), B(text="واریز TON", callback_data="d")],
        [B(text="سفارش‌ها", callback_data="o"), B(text="پشتیبانی", url=f"https://t.me/{SUPPORT}")],
    ])


def back():
    return InlineKeyboardMarkup(inline_keyboard=[[B(text="بازگشت", callback_data="m")]])


def user(u):
    db.execute("INSERT OR IGNORE INTO users(id,name) VALUES(?,?)", (u.id, u.full_name))
    db.commit()


async def show(c: CallbackQuery, text, kb):
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()


@dp.message(CommandStart())
async def start(m: Message):
    user(m.from_user)
    await m.answer("به ربات خوش آمدید. یکی از گزینه‌ها را انتخاب کنید.", reply_markup=menu())


@dp.callback_query(F.data == "m")
async def home(c: CallbackQuery):
    await show(c, "منوی اصلی", menu())


@dp.callback_query(F.data == "w")
async def wallet(c: CallbackQuery):
    user(c.from_user)
    bal, joined = db.execute("SELECT balance, joined FROM users WHERE id=?", (c.from_user.id,)).fetchone()
    await show(c, f"کیف پول شما\n\nشناسه: {c.from_user.id}\nنام: {c.from_user.full_name}\n"
                  f"موجودی: {ton(bal)} TON\nتاریخ عضویت: {joined[:10]}", back())


@dp.callback_query(F.data == "d")
async def deposit(c: CallbackQuery):
    rows = [[B(text=str(a), callback_data=f"d:{a}") for a in AMOUNTS[i:i + 4]] for i in range(0, 8, 4)]
    rows.append([B(text="بازگشت", callback_data="m")])
    await show(c, "مبلغ واریز را انتخاب کنید (TON):", InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("d:"))
async def make_order(c: CallbackQuery):
    user(c.from_user)
    amt = float(c.data[2:])
    if amt not in AMOUNTS:
        return await c.answer("مبلغ نامعتبر", show_alert=True)
    nano = int(amt * 1e9)
    code = "TD-" + secrets.token_hex(3).upper()
    db.execute("INSERT INTO orders(user_id,code,amount) VALUES(?,?,?)", (c.from_user.id, code, nano))
    db.commit()
    link = f"https://app.tonkeeper.com/transfer/{WALLET}?amount={nano}&text={code}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[B(text="پرداخت در Tonkeeper", url=link)],
                                               [B(text="بازگشت", callback_data="m")]])
    await show(c, f"درخواست واریز ثبت شد\n\nمبلغ: {amt:g} TON\nکد پیگیری (Comment): {code}\n\nآدرس:\n{WALLET}\n\n"
                  "کد پیگیری باید حتماً در Comment تراکنش باشد. بعد از تأیید، موجودی شما خودکار شارژ می‌شود.", kb)


@dp.callback_query(F.data == "o")
async def orders(c: CallbackQuery):
    rows = db.execute("SELECT code,amount,status,created FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",
                      (c.from_user.id,)).fetchall()
    if not rows:
        return await show(c, "هنوز سفارشی ثبت نشده است.", back())
    lines = [f"{code} · {ton(a)} TON · {'تأیید شد' if s == 'paid' else 'در انتظار'} · {t[:16]}" for code, a, s, t in rows]
    await show(c, "سفارش‌های شما\n\n" + "\n".join(lines), back())


async def watch():
    """هر ۱۵ ثانیه تراکنش‌های ورودی کیف پول را می‌خواند و با کد پیگیری تطبیق می‌دهد."""
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    while True:
        try:
            async with aiohttp.ClientSession(headers=headers) as s:
                async with s.get("https://toncenter.com/api/v2/getTransactions",
                                 params={"address": WALLET, "limit": 40}) as r:
                    data = await r.json()
            for t in data.get("result", []):
                msg = t.get("in_msg") or {}
                code = (msg.get("message") or "").strip()
                val = int(msg.get("value") or 0)
                h = t["transaction_id"]["hash"]
                row = db.execute("SELECT id,user_id,amount FROM orders WHERE code=? AND status='pending'", (code,)).fetchone()
                if row and val >= row[2]:
                    db.execute("UPDATE orders SET status='paid', tx=?, paid=CURRENT_TIMESTAMP WHERE id=?", (h, row[0]))
                    db.execute("UPDATE users SET balance=balance+? WHERE id=?", (val, row[1]))
                    db.commit()
                    await bot.send_message(row[1], f"واریز {ton(val)} TON تأیید شد.\nکد پیگیری: {code}\n"
                                                   f"هش تراکنش: {h}\nپشتیبانی: @{SUPPORT}", reply_markup=menu())
        except Exception as e:
            print("watch error:", e)
        await asyncio.sleep(15)


async def main():
    asyncio.create_task(watch())
    await dp.start_polling(bot)


asyncio.run(main())
