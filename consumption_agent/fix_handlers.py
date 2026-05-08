#!/usr/bin/env python3
import re

# Читаем файл
with open('telegram_bot.py', 'r') as f:
    content = f.read()

# Исправляем порядок: сначала регистрируем обработчики, потом добавляем check_access
fixed_content = re.sub(
    r'(app = Application\.builder\(\)\.token\(TOKEN\)\.build\(\)\s+)(.*?)(log\.info\(f"Зарегистрированы обработчики.*?")(.*?)(app\.add_handler\(CommandHandler\(\'start\', start\)\))(.*?)(app\.add_handler\(MessageHandler\(filters\.PHOTO, photo_handler\)\))',
    r'\1\n    # Сначала регистрируем обработчики\n    \5\6\7\n\n    \3\n\n    async def check_access(update: Update, ctx: ContextTypes.DEFAULT_TYPE):\n        if update.effective_chat.id not in ALLOWED_CHAT_IDS:\n            log.warning(f"Доступ запрещён для chat_id={update.effective_chat.id}")\n            await update.message.reply_text(\'❌ Доступ запрещён.\')\n            return False\n        log.info(f"Доступ разрешён для chat_id={update.effective_chat.id}")\n        return True\n\n    # Затем добавляем проверку доступа ко всем обработчикам\n    for handler in app.handlers:\n        if isinstance(handler, (CommandHandler, MessageHandler)):\n            original_callback = handler.callback\n            async def wrapped_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):\n                if await check_access(update, ctx):\n                    await original_callback(update, ctx)\n            handler.callback = wrapped_callback',
    content, flags=re.DOTALL
)

# Сохраняем исправленный файл
with open('telegram_bot.py', 'w') as f:
    f.write(fixed_content)

print("Исправлен порядок регистрации обработчиков.")