# Тарифы и оплата

**Назначение:** бесплатный лимит для новых пользователей бота, оплата тарифов через Telegram Stars, водяной знак на бесплатном тарифе.

## Как работает

1. **Бесплатно — 2 презентации** (`User.free_limit`, `src/db/models.py`). Перед генерацией бот проверяет `User.can_generate` (`main._confirm_and_generate`). Лимит исчерпан — сообщение и кнопки тарифов.
2. **Тарифы** — `PLANS` в `src/main.py`: Starter — 400 ⭐, Pro — 800 ⭐. Кнопка «⭐ …» отправляет счёт (`bot.send_invoice`, валюта `XTR` — Telegram Stars). После `successful_payment` — `db/session.upgrade_user_plan`: `users.plan` и строка в `payments` с `telegram_payment_charge_id`.
3. **Водяной знак:** у бесплатного тарифа PDF рендерится из копии PPTX с колонтитулом «Fibonacci.»; PPTX — без знака (`render.md`). У платного — без знака. У клиента API знак задаётся флагом клиента (`api_clients.watermark`, `/apikey watermark`).
4. **Списание генерации** — `users.presentations_count += 1`:
   - новый движок — в воркере после успешной отправки файлов (`db/session.count_generation`), колода помечается `decks.counted`;
   - старый движок (питч-дек) — `db/session.record_presentation` вместе со строкой `presentations`.
5. **Темы оформления** — все бесплатны всем в фазе 1 (`dialog.theme_allowed`, D-040).

`/plan` и кнопка «💳 Тарифы» показывают тариф и счётчик.

## Где в коде

- `src/main.py` — `PLANS`, `kb_paywall`, `cmd_plan`, `on_pay`, `on_pre_checkout`, `on_successful_payment`, проверка лимита в `_confirm_and_generate`.
- `src/db/models.py` — `User` (`plan`, `presentations_count`, `free_limit`, `can_generate`, `presentations_left`), `Payment`.
- `src/db/session.py` — `upgrade_user_plan`, `count_generation`, `record_presentation`.
- Лимиты клиентов API — отдельная механика (`rest-api.md`).

## Нюансы

- **Сейчас бот закрыт** (`bot-dialog.md`, D-081): им пользуется только владелец, остальных бот отправляет в основной бот. Поэтому лимиты и оплата в этом боте сейчас не работают на пользователей — решение владельца, ТЗ 11, вопрос 13.
- **Лимит платных тарифов в коде не проверяется.** Счёт и сообщение обещают «15 презентаций» (Starter) и «50 презентаций» (Pro), но `User.can_generate` для любого платного плана возвращает `True`, а `presentations_left` — 999. Оплативший Starter генерирует без ограничения.
- **Тариф выдаётся навсегда:** срока у плана нет, подписки нет. Как быть с оплатившими разово при переходе на подписку — открытый вопрос владельцу (ТЗ, раздел 11, вопрос 8).
- **Правила лимита живут в ORM-модели** (`User.free_limit`, `can_generate`) и в `main.py`. Правка лимита — в двух местах: число 2 стоит ещё и в текстах `cmd_plan` и `_confirm_and_generate`.
- **`WATERMARK_FREE_TIER`** есть в `src/config.py` и `.env.example`, но код его не читает: знак включается по `users.plan == "free"`.
- **Без БД** бот генерирует с водяным знаком и без проверки лимита (`get_session()` отдаёт `None`).
- **Лимит списывается только после доставки:** упавшая или не отправленная колода генерацию не тратит.

## Что не разобрано

- Возвраты Stars и повторная доставка `successful_payment` не обрабатываются явно — поведение при повторе не проверялось.
