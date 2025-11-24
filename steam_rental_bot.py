import os
import time
import threading
import logging
import json
from typing import Dict, Any, Optional, Set
import yaml
import asyncio

from FunPayAPI import Account, types
import steam.guard
from fastapi import FastAPI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('steam_rental_bot.log', encoding='utf-8'),
              logging.StreamHandler()]
)
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

active_rentals: Dict[int, Dict[str, Any]] = {}
user_states: Dict[int, Dict[str, Any]] = {}
pending_contact_messages: Set[int] = set()

funpay_account: Optional[Account] = None


class SteamRentalBot:
    def __init__(self):
        self.accounts = self.load_yaml(ACCOUNTS_FILE)
        self.funpay_token = FUNPAY_TOKEN
        self.funpay_account: Optional[Account] = None
        self.app_fastapi = FastAPI()
        # thread-safety lock for shared state (accounts, active_rentals)
        self.lock = threading.RLock()

        # Initialize Telegram application only if token present
        if TELEGRAM_TOKEN:
            try:
                self.application = Application.builder().token(TELEGRAM_TOKEN).build()
                self.setup_handlers()
            except Exception as e:
                logger.error(f"Не удалось создать Telegram Application: {e}")
                self.application = None
        else:
            logger.warning("TELEGRAM_TOKEN не задан — Telegram функции отключены")
            self.application = None
        self.setup_fastapi()

    # ----------------- YAML -----------------
    def load_yaml(self, path: str):
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        return {}

    def save_yaml(self, path: str, data: dict):
        with open(path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(data, f, allow_unicode=True)

    # Сохраняем токен FunPay в secrets.yaml
    def save_secrets(self):
        secrets_data = {
            "telegram_token": TELEGRAM_TOKEN,
            "admin_chat_id": ADMIN_CHAT_ID,
            "funpay_token": self.funpay_token
        }
        with open(SECRETS_FILE, 'w', encoding='utf-8') as f:
            yaml.safe_dump(secrets_data, f, allow_unicode=True)

    # ----------------- Handlers -----------------
    def setup_handlers(self):
        if not self.application:
            return
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("myid", self.myid_command))
        self.application.add_handler(CommandHandler("add_account", self.add_account_command))
        self.application.add_handler(CommandHandler("list_accounts", self.list_accounts))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("set_funpay_token", self.set_funpay_token))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

    # ----------------- FastAPI -----------------
    def setup_fastapi(self):
        @self.app_fastapi.get("/ping")
        async def ping():
            return {"status": "✅ ok"}

    # ----------------- Telegram Commands -----------------
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_CHAT_ID:
            return
        await update.message.reply_text("👋 Бот активен! Используйте /myid чтобы узнать ваш chat_id.")

    async def myid_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(f"🆔 Ваш chat_id: {update.effective_chat.id}")

    async def set_funpay_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_CHAT_ID:
            return
        if not context.args:
            await update.message.reply_text("❌ Формат: /set_funpay_token <token>")
            return
        self.funpay_token = context.args[0]
        self.save_secrets()
        await update.message.reply_text("✅ FunPay токен установлен. Перезапустите бота.")
        logger.info("FunPay токен установлен")

    async def add_account_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_CHAT_ID:
            return
        user_states[update.effective_user.id] = {'state': 'waiting_login', 'data': {}}
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="cancel_add")]]
        await update.message.reply_text("📝 Введите логин Steam:", reply_markup=InlineKeyboardMarkup(keyboard))

    async def list_accounts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_CHAT_ID:
            return
        if not self.accounts:
            await update.message.reply_text("📋 Пусто")
            return
        lines = [f"🎮 {login}: {','.join(data.get('games', []))} ({'🟢 Свободен' if data.get('status')=='free' else '🔴 Занят'})"
                 for login, data in self.accounts.items()]
        await update.message.reply_text("\n".join(lines))

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_CHAT_ID:
            return
        if not active_rentals:
            await update.message.reply_text("📊 Нет активных аренд")
            return
        lines = [f"🆔 Чат {chat_id}: {r['login']} ⏳ {max(0,int((r['end_time']-time.time())/60))} мин"
                 for chat_id,r in active_rentals.items()]
        await update.message.reply_text("\n".join(lines))

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        if q.data == "cancel_add":
            uid = q.from_user.id
            if uid in user_states:
                del user_states[uid]
            await q.edit_message_text("❌ Добавление аккаунта отменено")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        text = update.message.text

        # ------------------ Админ добавление аккаунта ------------------
        if uid == ADMIN_CHAT_ID and uid in user_states:
            st = user_states[uid]['state']
            data = user_states[uid]['data']
            k = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="cancel_add")]])

            if st == 'waiting_login':
                if text in self.accounts:
                    await update.message.reply_text("❌ Аккаунт уже существует")
                    return
                data['login'] = text
                user_states[uid]['state'] = 'waiting_password'
                await update.message.reply_text("🔒 Введите пароль Steam:", reply_markup=k)
            elif st == 'waiting_password':
                data['password'] = text
                user_states[uid]['state'] = 'waiting_mafile'
                await update.message.reply_text("📂 Введите путь к mafile:", reply_markup=k)
            elif st == 'waiting_mafile':
                data['mafile_path'] = text
                user_states[uid]['state'] = 'waiting_games'
                await update.message.reply_text("🎮 Введите игры через запятую:", reply_markup=k)
            elif st == 'waiting_games':
                data['games'] = [g.strip() for g in text.split(',')]
                user_states[uid]['state'] = 'waiting_api_key'
                await update.message.reply_text("🔑 Введите Steam API ключ:", reply_markup=k)
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
                await update.message.reply_text(f"✅ Аккаунт {login} добавлен 🎉")
                del user_states[uid]

    # ----------------- Вспомогательные -----------------
    def get_free_account(self) -> Optional[str]:
        with self.lock:
            for login, data in self.accounts.items():
                if data.get('status') == 'free':
                    return login
        return None

    def generate_steam_guard_code(self, mafile_path: str) -> Optional[str]:
        try:
            with open(mafile_path, 'r') as f:
                data = json.load(f)
            return steam.guard.generate_code(data['shared_secret'])
        except Exception as e:
            logger.error(f"Ошибка Steam Guard: {e}")
            return None

    def change_password(self, login: str) -> bool:
        logger.info(f"🔑 Смена пароля {login} (заглушка)")
        return True

    def send_telegram_notification(self, message: str):
        if not self.application:
            logger.info(f"Telegram уведомление пропущено (бот не инициализирован): {message}")
            return

        coro = self.application.bot.send_message(chat_id=ADMIN_CHAT_ID, text=message)
        # Try to schedule in an existing running loop if available, otherwise run synchronously
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None

        try:
            if loop and loop.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(coro, loop)
                except Exception:
                    loop.create_task(coro)
            else:
                # No running loop in this thread — run the coroutine safely
                asyncio.run(coro)
        except Exception as e:
            logger.error(f"Не удалось отправить Telegram уведомление: {e}")

    # ----------------- Мониторы -----------------
    def rental_monitor(self):
        while True:
            try:
                now = time.time()
                expired = []
                # copy keys to avoid runtime change issues
                with self.lock:
                    for chat_id, rental in list(active_rentals.items()):
                        remaining = rental['end_time'] - now
                        if remaining <= 0:
                            expired.append(chat_id)
                    for chat_id in expired:
                        login = active_rentals[chat_id]['login']
                        self.change_password(login)
                        if login in self.accounts:
                            self.accounts[login]['status'] = 'free'
                            self.save_yaml(ACCOUNTS_FILE, self.accounts)
                        try:
                            del active_rentals[chat_id]
                        except KeyError:
                            pass
                        self.send_telegram_notification(f"🏁 Аренда для {login} завершена")
            except Exception as e:
                logger.error(f"Ошибка мониторинга аренды: {e}")
            time.sleep(60)

    # ----------------- FunPay -----------------
    def start_funpay_listener(self):
        global funpay_account
        if not self.funpay_token:
            self.send_telegram_notification("⚠️ Установите FunPay токен: /set_funpay_token")
            return
        try:
            funpay_account = Account(self.funpay_token, raise_on_error=True)
            funpay_account.add_event_handler(types.EventTypes.NEW_ORDER, self.handle_new_order)
            funpay_account.add_event_handler(types.EventTypes.NEW_MESSAGE, self.handle_new_message)
            self.send_telegram_notification("✅ FunPay подключен успешно!")
            funpay_account.listen()
        except Exception as e:
            logger.error(f"❌ Ошибка FunPay: {e}")
            self.send_telegram_notification(f"❌ Ошибка FunPay: {e}")

    def handle_new_order(self, order):
        chat_id = order.chat_id
        free_login = self.get_free_account()
        if not free_login:
            order.send_message("🚫 Нет свободных аккаунтов")
            self.send_telegram_notification(f"❌ Нет свободных аккаунтов для заказа {order.id}")
            return
        with self.lock:
            account_data = self.accounts.get(free_login)
            if not account_data:
                order.send_message("🚫 Ошибка: аккаунт не найден")
                return
            account_data['status'] = 'rented'
            self.save_yaml(ACCOUNTS_FILE, self.accounts)
            order.send_message(
                f"👋 Ваш аккаунт:\n🔑 Логин: {free_login}\n🔒 Пароль: {account_data.get('password')}\n📲 !код для Steam Guard"
            )
            active_rentals[chat_id] = {
                'login': free_login,
                'end_time': time.time() + 3600,
                'api_key': account_data.get('api_key'),
                'order_id': order.id,
                'bonus_given': False
            }
        buyer = getattr(order, 'buyer', None)
        buyer_name = getattr(buyer, 'username', str(buyer))
        self.send_telegram_notification(f"🆕 Новый заказ {order.id} от {buyer_name}")

    def handle_new_message(self, message):
        chat_id = message.chat_id
        if chat_id not in active_rentals:
            message.send("🚫 Аккаунт не в аренде")
            return
        logger.info(f"📩 Новое сообщение FunPay в чате {chat_id}: {message.text}")

    # ----------------- Запуск -----------------
    def run(self):
        threading.Thread(target=lambda: uvicorn.run(self.app_fastapi, host="0.0.0.0", port=8000), daemon=True).start()
        threading.Thread(target=self.rental_monitor, daemon=True).start()
        threading.Thread(target=self.start_funpay_listener, daemon=True).start()
        if self.application:
            self.application.run_polling()
        else:
            logger.warning("Telegram polling не запущен (бот не инициализирован).")


if __name__ == "__main__":
    SteamRentalBot().run()
