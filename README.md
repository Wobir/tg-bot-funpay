# Steam Rental Bot для FunPay

Telegram бот для автоматической аренды Steam аккаунтов через FunPay API.

## Функции

### FunPay интеграция
- ✅ Автоматическая выдача аккаунтов при заказе
- ✅ Обработка команд: !код, !время, !игры, !помощь, !связь
- ✅ Генерация Steam Guard кодов
- ✅ Связь с продавцом через бота

### Telegram управление
- ✅ /set_funpay_token - установка FunPay токена
- ✅ /add_account - добавление Steam аккаунта
- ✅ /list_accounts - список аккаунтов
- ✅ /status - активные аренды

### Мониторинг
- ✅ Таймер аренды (1 час по умолчанию)
- ✅ Предупреждения за 30, 20, 10 минут
- ✅ Автоматическая смена пароля после аренды
- ✅ Бонус +30 минут за отзыв

### 24/7 поддержка
- ✅ Flask сервер для пинга (/ping)
- ✅ Подробное логирование
- ✅ Уведомления в Telegram

## Установка

1. Установите Python 3.10+
2. Установите зависимости:
\`\`\`bash
pip install -r requirements.txt
\`\`\`

3. Запустите бота:
\`\`\`bash
python steam_rental_bot.py
\`\`\`

4. В Telegram выполните:
   - `/set_funpay_token YOUR_GOLDEN_KEY`
   - `/add_account` для добавления Steam аккаунтов

# Установка и запуск Steam Rental Bot на Windows

## 1. Установка Python 3.10+
1. Скачайте установщик Python с официального сайта: [https://www.python.org/downloads/](https://www.python.org/downloads/)
2. Запустите установщик.
3. Обязательно отметьте опцию **Add Python to PATH**.
4. Нажмите **Install Now** и дождитесь завершения установки.
5. Проверьте установку в командной строке:
\`\`\`cmd
python --version
\`\`\`

## 2. Установка зависимостей
1. Откройте Командную строку (Win + R → cmd → Enter) или PowerShell.
2. Перейдите в папку с проектом:
   cd путь\к\папке\с\ботом
3. Установите зависимости:
   pip install -r requirements.txt

## 3. Первичная настройка
1. Получите Golden Key: FunPay → Код элемента → Storage → golde_key Value
2. Создайте ТГ-бота через @BotFather и сохраните токен созданного бота.

## 4. Запуск бота

python steam_rental_bot.py

## 5. Настройка через Telegram

1. В Telegram откройте чат с ботом.
2. Выполните команду для установки токена:
   /set_funpay_token YOUR_GOLDEN_KEY
3. Узнайте ваш UID через /myid -> Поставьте этот ID в secrets.yaml admin_chat_id: ЭТО НУЖНО ДЛЯ БЕЗОПАСНОСТИ
4. Перезапустите steam_rental_bot.py
5. Добавьте Steam аккаунты:
   /add_account

## Структура файлов

- `steam_rental_bot.py` - основной код бота
- `accounts.yaml` - база Steam аккаунтов
- `secrets.yaml` - конфигурация (FunPay/tg токен)
- `steam_rental_bot.log` - логи работы
- `mafiles/` - папка для Steam Guard файлов

## Безопасность

- Доступ к командам только для ADMIN_CHAT_ID
- Логирование всех операций
- Игнорирование некомандных сообщений в FunPay
