# improved code without comments
import asyncio
import logging
import os
import threading
import time
from datetime import datetime
from typing import Dict, Any, Optional
import yaml
import steam.guard
from FunPayAPI import Account, types
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, CallbackContext

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', handlers=[logging.FileHandler('steam_rental_bot.log', encoding='utf-8'), logging.StreamHandler()])
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = "8029226459:AAHkgJN-dZXuDF20kB7n5FCdhhbW0yKnu5M"
ADMIN_CHAT_ID = 7890395437
ACCOUNTS_FILE = "accounts.yaml"
CONFIG_FILE = "config.yaml"

funpay_account = None
active_rentals: Dict[int, Dict[str, Any]] = {}
user_states: Dict[int, Dict[str, Any]] = {}
pending_contact_messages = set()

class SteamRentalBot:
    def __init__(self):
        self.updater = Updater(TELEGRAM_TOKEN, use_context=True)
        self.dp = self.updater.dispatcher
        self.setup_handlers()
        self.load_config()
        self.load_accounts()
        self.app = Flask(__name__)
        self.setup_flask()

    def setup_handlers(self):
        self.dp.add_handler(CommandHandler("start", self.start_command))
        self.dp.add_handler(CommandHandler("myid", self.myid_command))
        self.dp.add_handler(CommandHandler("set_funpay_token", self.set_funpay_token))
        self.dp.add_handler(CommandHandler("add_account", self.add_account_command))
        self.dp.add_handler(CommandHandler("list_accounts", self.list_accounts))
        self.dp.add_handler(CommandHandler("status", self.status_command))
        self.dp.add_handler(CallbackQueryHandler(self.button_callback))
        self.dp.add_handler(MessageHandler(Filters.text & ~Filters.command, self.handle_message))

    def setup_flask(self):
        @self.app.route('/ping')
        def ping():
            return "OK"

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}
                self.funpay_token = config.get('funpay_token')
        else:
            self.funpay_token = None

    def save_config(self):
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            yaml.safe_dump({'funpay_token': self.funpay_token}, f, allow_unicode=True)

    def load_accounts(self):
        if os.path.exists(ACCOUNTS_FILE):
            with open(ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
                self.accounts = yaml.safe_load(f) or {}
        else:
            self.accounts = {}

    def save_accounts(self):
        with open(ACCOUNTS_FILE, 'w', encoding='utf-8') as f:
            yaml.safe_dump(self.accounts, f, allow_unicode=True)

    def is_admin(self, user_id: int) -> bool:
        return user_id == ADMIN_CHAT_ID

    def start_command(self, update: Update, context: CallbackContext):
        if not self.is_admin(update.effective_user.id):
            return
        update.message.reply_text("Бот активен")

    def myid_command(self, update: Update, context: CallbackContext):
        update.message.reply_text(str(update.effective_chat.id))

    def set_funpay_token(self, update: Update, context: CallbackContext):
        if not self.is_admin(update.effective_user.id):
            return
        if not context.args:
            update.message.reply_text("формат: /set_funpay_token token")
            return
        self.funpay_token = context.args[0]
        self.save_config()
        update.message.reply_text("токен установлен")

    def add_account_command(self, update: Update, context: CallbackContext):
        if not self.is_admin(update.effective_user.id):
            return
        user_states[update.effective_user.id] = {'state': 'waiting_login', 'data': {}}
        keyboard = [[InlineKeyboardButton("Отмена", callback_data="cancel_add")]]
        update.message.reply_text("логин:", reply_markup=InlineKeyboardMarkup(keyboard))

    def list_accounts(self, update: Update, context: CallbackContext):
        if not self.is_admin(update.effective_user.id):
            return
        if not self.accounts:
            update.message.reply_text("пусто")
            return
        msg = []
        for login, data in self.accounts.items():
            msg.append(f"{login}: {','.join(data.get('games', []))} {data.get('status')}")
        update.message.reply_text("\n".join(msg))

    def status_command(self, update: Update, context: CallbackContext):
        if not self.is_admin(update.effective_user.id):
            return
        if not active_rentals:
            update.message.reply_text("нет активных")
            return
        lines = []
        for chat_id, rental in active_rentals.items():
            remaining = max(0, int((rental['end_time'] - time.time()) / 60))
            lines.append(f"{chat_id}: {rental['login']} {remaining} мин")
        update.message.reply_text("\n".join(lines))

    def button_callback(self, update: Update, context: CallbackContext):
        q = update.callback_query
        q.answer()
        if q.data == "cancel_add":
            uid = q.from_user.id
            if uid in user_states:
                del user_states[uid]
            q.edit_message_text("отменено")

    def handle_message(self, update: Update, context: CallbackContext):
        if not self.is_admin(update.effective_user.id):
            return
        uid = update.effective_user.id
        if uid not in user_states:
            return
        text = update.message.text
        st = user_states[uid]['state']
        data = user_states[uid]['data']
        k = InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="cancel_add")]])
        if st == 'waiting_login':
            if text in self.accounts:
                update.message.reply_text("уже есть")
                return
            data['login'] = text
            user_states[uid]['state'] = 'waiting_password'
            update.message.reply_text("пароль:", reply_markup=k)
        elif st == 'waiting_password':
            data['password'] = text
            user_states[uid]['state'] = 'waiting_mafile'
            update.message.reply_text("путь mafile:", reply_markup=k)
        elif st == 'waiting_mafile':
            data['mafile_path'] = text
            user_states[uid]['state'] = 'waiting_games'
            update.message.reply_text("игры через запятую:", reply_markup=k)
        elif st == 'waiting_games':
            data['games'] = [g.strip() for g in text.split(',')]
            user_states[uid]['state'] = 'waiting_api_key'
            update.message.reply_text("steam api key:", reply_markup=k)
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
            self.save_accounts()
            update.message.reply_text(f"добавлен {login}")
            del user_states[uid]

    def get_free_account(self) -> Optional[str]:
        for login, d in self.accounts.items():
            if d.get('status') == 'free':
                return login
        return None

    def generate_steam_guard_code(self, path: str) -> Optional[str]:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                mf = yaml.safe_load(f)
            return steam.guard.generate_code(mf['shared_secret'])
        except:
            return None

    def change_password(self, login: str) -> bool:
        return True

    def send_telegram_notification(self, m: str):
        try:
            self.updater.bot.send_message(chat_id=ADMIN_CHAT_ID, text=m)
        except:
            pass

    def handle_new_order(self, order):
        try:
            chat_id = order.chat_id
            free_login = self.get_free_account()
            if not free_login:
                order.send_message("нет свободных")
                self.send_telegram_notification(f"нет аккаунтов {order.id}")
                return
            acc = self.accounts[free_login]
            acc['status'] = 'rented'
            self.save_accounts()
            order.send_message(f"логин: {free_login}\nпароль: {acc['password']}")
            end_time = time.time() + 3600
            active_rentals[chat_id] = {
                'login': free_login,
                'end_time': end_time,
                'api_key': acc['api_key'],
                'order_id': order.id,
                'bonus_given': False
            }
            self.send_telegram_notification(f"новый {order.id}")
        except:
            pass

    def handle_new_message(self, message):
        try:
            chat_id = message.chat_id
            text = message.text.strip().lower()
            if chat_id not in active_rentals:
                message.send("не актив")
                return
            r = active_rentals[chat_id]
            acc = self.accounts[r['login']]
            if text in ['!код']:
                c = self.generate_steam_guard_code(acc['mafile_path'])
                if c:
                    message.send(c)
            elif text == '!время':
                remaining = max(0, int(r['end_time'] - time.time()))
                m = remaining // 60
                s = remaining % 60
                message.send(f"{m}:{s}")
            elif text == '!игры':
                message.send(",".join(acc.get('games', [])))
            elif text == '!помощь':
                message.send("!код !время !игры !связь")
            elif text == '!связь':
                pending_contact_messages.add(chat_id)
                message.send("введите текст")
            elif chat_id in pending_contact_messages:
                self.send_telegram_notification(f"{chat_id}: {text}")
                message.send("отправлено")
                pending_contact_messages.discard(chat_id)
        except:
            pass

    def rental_monitor(self):
        while True:
            try:
                now = time.time()
                exp = []
                for chat_id, r in active_rentals.items():
                    if r['end_time'] <= now:
                        exp.append(chat_id)
                for chat_id in exp:
                    r = active_rentals[chat_id]
                    login = r['login']
                    self.change_password(login)
                    if funpay_account:
                        funpay_account.send_message(chat_id, "завершено")
                    self.accounts[login]['status'] = 'free'
                    self.save_accounts()
                    del active_rentals[chat_id]
            except:
                pass
            time.sleep(60)

    def bonus_monitor(self):
        while True:
            time.sleep(300)

    def start_funpay_listener(self):
        global funpay_account
        if not self.funpay_token:
            self.send_telegram_notification("установите токен")
            return
        try:
            funpay_account = Account(self.funpay_token, raise_on_error=True)
            funpay_account.add_event_handler(types.EventTypes.NEW_ORDER, self.handle_new_order)
            funpay_account.add_event_handler(types.EventTypes.NEW_MESSAGE, self.handle_new_message)
            self.send_telegram_notification("funpay ok")
            funpay_account.listen()
        except:
            self.send_telegram_notification("ошибка funpay")

    def run(self):
        self.updater.start_polling()
        threading.Thread(target=self.rental_monitor, daemon=True).start()
        threading.Thread(target=self.bonus_monitor, daemon=True).start()
        threading.Thread(target=self.start_funpay_listener, daemon=True).start()
        threading.Thread(target=lambda: self.app.run(host='0.0.0.0', port=5000), daemon=True).start()
        self.updater.idle()

if __name__ == "__main__":
    SteamRentalBot().run()
