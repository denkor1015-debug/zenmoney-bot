# ZenMoney Telegram Bot

Telegram-бот для зручного додавання фінансових транзакцій у ZenMoney через природну мову.

## Можливості

- 🗣️ Розпізнавання тексту через Claude Haiku (AI)
- 🔍 Автоматичне визначення категорії через ZenMoney Suggest API
- ✅ Підтвердження перед збереженням — бачиш що розпізналось
- 💳 Відображення балансів по рахунках
- 🔒 Захист через whitelist Telegram ID

## Налаштування

### 1. Отримай необхідні токени

| Токен | Де отримати |
|-------|-------------|
| ZenMoney Bearer Token | https://zerro.app/token |
| Telegram Bot Token | @BotFather у Telegram |
| Твій Telegram User ID | @userinfobot у Telegram |
| Anthropic API Key | https://console.anthropic.com |

### 2. Клонуй та налаштуй

```bash
cd zenmoney-bot
cp .env.example .env
# Відкрий .env та заповни всі значення
```

### 3. Встанови залежності

```bash
pip install -r requirements.txt
```

### 4. Запусти

```bash
python bot.py
```

---

## Команди бота

| Команда | Дія |
|---------|-----|
| `/start` | Підключення та перша синхронізація з ZenMoney |
| `/accounts` | Показати всі рахунки з поточними балансами |
| `/refresh` | Оновити рахунки та категорії з ZenMoney |

## Формат транзакцій

Надсилай будь-який текст — бот розбере:

```
кава 85
```
```
кава 85, аптека 120 монобанк, АТБ 340 готівка
```
```
отримав зарплату 15000 монобанк
```
```
вчора кава 75 та бургер 180
```
```
переказав 500 з монобанку на готівку
```

## Структура проєкту

```
zenmoney-bot/
├── .env              # Твої секрети (не комітити!)
├── .env.example      # Приклад змінних середовища
├── state.json        # Кеш даних ZenMoney (auto-created)
├── requirements.txt
├── bot.py            # Telegram bot handlers (aiogram 3.x)
├── zenmoney.py       # ZenMoney API client
├── parser.py         # Claude Haiku NLP parser
└── README.md
```

## Технічні деталі

- **API**: ZenMoney `/v8/diff/` + `/v8/suggest/`
- **AI**: Claude Haiku (`claude-haiku-4-5`) через Anthropic SDK
- **Sync**: `state.json` з atomic write (через `.tmp` → rename)
- **Security**: перевірка `from_user.id` у кожному хендлері

## Налаштування рахунку за замовчуванням

Відкрий `state.json` після першого `/start` і знайди `"defaultAccount"` — можна змінити вручну на ключ будь-якого рахунку (назва у нижньому регістрі).
