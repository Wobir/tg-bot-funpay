import os
import time
import threading
import logging
from typing import Dict, Any, Optional
import yaml
import steam.guard
from FunPayAPI import Account, types
from fastapi import FastAPI
import uvicorn
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[logging.FileHandler('steam_rental_bot.log', encoding='utf-8'),
                              logging.StreamHandler()])
logger = logging.getLogger(__name__)

SECRETS_FILE = "secrets.yaml"
ACCOUNTS_FILE = "accounts.yaml"

example_secrets = {
    "telegram_token": "YOUR_TELEGRAM_TOKEN",
    "admin_chat_id": 123456789,
    "funpay_token": "YOUR_FUNPAY_TOKEN"
}

example_accounts = {}

def ensure_file(path: str, example: dict):
    if not os.path.exists(path):
        with open(path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(example, f, allow_unicode=True)
        print(f"[INFO] {path} создан с примером. Замените значения на реальные.")

ensure_file(SECRETS_FILE, example_secrets)
ensure_file(ACCOUNTS_FILE, example_accounts)

with open(SECRETS_FILE, 'r', encoding='utf-8') as f:
    secrets = yaml.safe_load(f) or {}

TELEGRAM_TOKEN = secrets.get("telegram_token")
ADMIN_CHAT_ID = secrets.get("admin_chat_id")
FUNPAY_TOKEN = secrets.get("funpay_token")

if TELEGRAM_TOKEN == "YOUR_TELEGRAM_TOKEN" or FUNPAY_TOKEN == "YOUR_FUNPAY_TOKEN":
    print("[WARNING] Пожалуйста, замените примерные токены в secrets.yaml на реальные!")

active_rentals: Dict[int, Dict[str, Any]] = {}
user_states: Dict[int, Dict[str, Any]] = {}
pending_contact_messages = set()

class SteamRentalBot:
    def __init__(self):
        self.accounts = self.load_yaml(ACCOUNTS_FILE)
        self.funpay_account: Optional[Account] = None
        self.app_fastapi = FastAPI()
        self.application = Application.builder().token(TELEGRAM_TOKEN).build()
        self.setup_handlers()
        self.setup_fastapi()

    def load_yaml(self, path: str):
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        return {}

    def save_yaml(self, path: str, data: dict):
        with open(path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(data, f, allow_unicode=True)

    def setup_handlers(self):
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("myid", self.myid_command))
        self.application.add_handler(CommandHandler("add_account", self.add_account_command))
        self.application.add_handler(CommandHandler("list_accounts", self.list_accounts))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

    def setup_fastapi(self):
        @self.app_fastapi.get("/ping")
        async def ping():
            return {"status": "ok"}

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_CHAT_ID:
            return
        await update.message.reply_text("Бот активен")

    async def myid_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(str(update.effective_chat.id))

    async def add_account_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_CHAT_ID:
            return
        user_states[update.effective_user.id] = {'state': 'waiting_login', 'data': {}}
        keyboard = [[InlineKeyboardButton("Отмена", callback_data="cancel_add")]]
        await update.message.reply_text("логин:", reply_markup=InlineKeyboardMarkup(keyboard))

    async def list_accounts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_CHAT_ID:
            return
        if not self.accounts:
            await update.message.reply_text("пусто")
            return
        lines = [f"{login}: {','.join(data.get('games', []))} {data.get('status')}" for login, data in self.accounts.items()]
        await update.message.reply_text("\n".join(lines))

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_CHAT_ID:
            return
        if not active_rentals:
            await update.message.reply_text("нет активных")
            return
        lines = [f"{chat_id}: {r['login']} {max(0,int((r['end_time']-time.time())/60))} мин" for chat_id,r in active_rentals.items()]
        await update.message.reply_text("\n".join(lines))

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        if q.data == "cancel_add":
            uid = q.from_user.id
            if uid in user_states:
                del user_states[uid]
            await q.edit_message_text("отменено")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if uid != ADMIN_CHAT_ID or uid not in user_states:
            return
        text = update.message.text
        st = user_states[uid]['state']
        data = user_states[uid]['data']
        k = InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="cancel_add")]])
        if st == 'waiting_login':
            if text in self.accounts:
                await update.message.reply_text("уже есть")
                return
            data['login'] = text
            user_states[uid]['state'] = 'waiting_password'
            await update.message.reply_text("пароль:", reply_markup=k)
        elif st == 'waiting_password':
            data['password'] = text
            user_states[uid]['state'] = 'waiting_mafile'
            await update.message.reply_text("путь mafile:", reply_markup=k)
        elif st == 'waiting_mafile':
            data['mafile_path'] = text
            user_states[uid]['state'] = 'waiting_games'
            await update.message.reply_text("игры через запятую:", reply_markup=k)
        elif st == 'waiting_games':
            data['games'] = [g.strip() for g in text.split(',')]
            user_states[uid]['state'] = 'waiting_api_key'
            await update.message.reply_text("steam api key:", reply_markup=k)
        elif st == 'waiting_api_key':
            data['api_key'] = text
            login = data['login']
            self.accounts[login] = {
                'password': data['password'],
                'mafile_path': data['mafile_path'],
                'games': data['games'],
                'api_key': data['api_key'],
                'status': 'free'
            }
            self.save_yaml(ACCOUNTS_FILE, self.accounts)
            await update.message.reply_text(f"добавлен {login}")
            del user_states[uid]

    def run(self):
        threading.Thread(target=lambda: uvicorn.run(self.app_fastapi, host="0.0.0.0", port=8000), daemon=True).start()
        self.application.run_polling()

if __name__ == "__main__":
    SteamRentalBot().run()
