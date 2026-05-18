---
name: email-access
description: Доступ ко всем 4 почтовым ящикам (Gmail, Yandex, Mail.ru Zorea, Mail.ru Neutrinon) и SMS через Phone Link для сканирования почт на чеки и расходы, включая релевантные IMAP-папки (`INBOX`, `Spam/Junk`, папки чеков/Receipts), импорта в БД consumption.db, проверки SMS на двух телефонах, добавления покупок по данным из писем.
---

# Email Access — Доступ к почтам и SMS

## Важно: исходящая рабочая почта

Этот skill — для чтения почты, IMAP-сканирования чеков и SMS. Для отправки писем на рабочую почту **не использовать Gmail API connector как первый путь**: в текущей среде он часто падает с `ACCESS_TOKEN_SCOPE_INSUFFICIENT`.

Для исходящих писем всегда использовать skill `email-send`:

```powershell
& 'C:\Users\Yuri Artamonov\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  skills/email-send/scripts/send_email_smtp.py `
  --to "yu.v.artamonov@gmail.com" `
  --subject "Subject" `
  --body "Message body"
```

Если пользователь говорит "рабочая почта" без адреса, это `yu.v.artamonov@gmail.com`.

## Доступные почтовые ящики

| # | Ящик | Пароль | IMAP |
|---|------|--------|------|
| 1 | **yu.v.artamonov@gmail.com** | `GMAIL_APP_PASSWORD` из `.env` (с пробелами) | imap.gmail.com:993 |
| 2 | **HKID2021@yandex.ru** | `YANDEX_APP_PASSWORD` из `.env` | imap.yandex.ru:993 |
| 3 | **zorea2001@mail.ru** | `MAILRU_ZOREA_PASSWORD` из `.env` | imap.mail.ru:993 |
| 4 | **neutrinon@mail.ru** | `MAILRU_NEUTRINON_PASSWORD` из `.env` | imap.mail.ru:993 |

## Пароли — как брать

```python
# Все пароли из .env, надо чистить кавычки и ПРОБЕЛЫ:
pwd = os.environ.get('GMAIL_APP_PASSWORD', '').replace('"', '').replace(' ', '')
# Без очистки пробелов Gmail не работает!
```

Пароли в `.env` — с пробелами и кавычками. При использовании ОБЯЗАТЕЛЬНО очищать:
- `replace('"', '')` — убрать кавычки
- `replace(' ', '')` — убрать пробелы (иначе ошибка логина)

## SMS — Phone Link

SMS с двух телефонов читаются через SQLite-базу Microsoft Phone Link:

```
/mnt/c/Users/*/AppData/Local/Packages/Microsoft.YourPhone_8wekyb3d8bbwe/LocalCache/Indexed/*/System/Database/phone.db
```

Конвертация Windows FILETIME → datetime:
```python
def windows_ticks_to_datetime(value):
    unix_seconds = (int(value) - 116444736000000000) / 10_000_000
    return datetime.fromtimestamp(unix_seconds)
```

Базу надо копировать в temp (с -wal, -shm) — основной файл может быть заблокирован.

## Сканирование чеков — готовый скрипт

`daily_cheque_scan.py` — ежедневное сканирование всех 4 почт + SMS:

```bash
cd ~/.openclaw/workspace/consumption_agent
source venv/bin/activate
source .env
python3 daily_cheque_scan.py
```

Скрипт:
1. Подключается ко всем 4 почтам через IMAP
2. Обходит релевантные папки: `INBOX`, `Spam/Junk/Спам`, папки чеков вроде `Receipts` / `Checks` / `чеки`
3. Ищет письма за сегодня (+ вчера для первой синхронизации)
4. Определяет магазин по отправителю (Самокат, Ozon, WB, Яндекс и т.д.)
5. Парсит HTML-чеки Платформы ОФД (сумма, дата, товары)
6. Для писем без HTML-чека — ищет сумму регулярками
7. Сканирует SMS из Phone Link на предмет расходов
8. Добавляет записи в `purchases`
9. Дополнительно режет дубли между папками по `Message-ID`
10. Логирует всё в `logs/daily_cheque_scan.log`

## Дедупликация email + SMS

Для расходов используется общий helper `consumption_agent/purchase_dedup.py`:
- межпапочная дедупликация email по `Message-ID`
- межисточниковая дедупликация email/SMS по дате, каноническому магазину и времени операции
- нормализация алиасов продавца, например `УМНЫЙ РИТЕЙЛ` и `Самокат` считаются одним магазином
- если email-чек содержит отдельную строку `доставка`, то дедуп сравнивает и полный итог, и сумму без доставки
- без времени операции запись автоматически не схлопывается, чтобы не убить разные покупки одного дня

## Определение магазинов

Ключевые слова для автоопределения магазина:
- `ozon` → Ozon
- `wildberries` → Wildberries
- `я.маркет`, `yandex.market` → Яндекс Маркет
- `самокат`, `samokat.ru`, `умный ритейл` → Самокат
- `куш`, `кушай` → Кушай на районе
- `лавка`, `lavka` → Яндекс Лавка
- `я.еда`, `yandex.food` → Яндекс Еда
- `я.плюс`, `yandex plus` → Яндекс Плюс
- `kfc`, `вкусно и точка`, `burger king`, `магнит`, `пятёрочка` и т.д.

Чеки Платформы ОФД (`chek.pofd.ru`) парсятся детально: магазин, дата, сумма, товарные позиции.
Чеки Яндекс Чека (`check.yandex`) — аналогично.

## Cron

Скрипт запускается ежедневно в 23:30 через cron:
```
30 23 * * * cd ... && source venv/bin/activate && source .env && python3 daily_cheque_scan.py
```

## При необходимости — ручной запуск

```python
# Подключение к одной почте:
import imaplib
from imap_folders import discover_target_mailboxes

imap = imaplib.IMAP4_SSL('imap.gmail.com', timeout=20)
imap.login('yu.v.artamonov@gmail.com', pwd)
folders = discover_target_mailboxes(imap)
for mailbox in folders:
    imap.select(f'"{mailbox}"', readonly=True)
    result, data = imap.search(None, '(ON 12-May-2026)')
```

## Примечания

- **VPN**: для доступа к IMAP с WSL должен быть включён VPN на Windows
- **Phone Link**: база доступна только из WSL (через /mnt/c/)
- **Дубли**: скрипт использует `purchase_dedup.py`, а не простую проверку по `(purchase_date, total_amount, store_name)`
- **SMS**: 59 900+ без названия магазина → отсеивается как перевод между счетами
