# Скрипт для настройки Windows на постоянную работу (без сна/гибернации)
# Запускать от имени Администратора
# Сохранён из WSL в ~/.openclaw/workspace/set-windows-keepalive.ps1

Write-Host "Настройка электропитания для постоянной работы..." -ForegroundColor Cyan

# 1. Отключить гибернацию
Write-Host "  [1/4] Отключение гибернации..." -NoNewline
try {
    powercfg /h off
    Write-Host " OK" -ForegroundColor Green
} catch {
    Write-Host " ОШИБКА: $_" -ForegroundColor Red
}

# 2. Спящий режим: Никогда (от сети)
Write-Host "  [2/4] Отключение сна при питании от сети..." -NoNewline
try {
    powercfg /change standby-timeout-ac 0
    Write-Host " OK" -ForegroundColor Green
} catch {
    Write-Host " ОШИБКА: $_" -ForegroundColor Red
}

# 3. Спящий режим: Никогда (от батареи)
Write-Host "  [3/4] Отключение сна при питании от батареи..." -NoNewline
try {
    powercfg /change standby-timeout-dc 0
    Write-Host " OK" -ForegroundColor Green
} catch {
    Write-Host " ОШИБКА: $_" -ForegroundColor Red
}

# 4. Отключение таймеров сна гибернации (если активна)
Write-Host "  [4/4] Отключение гибернации по таймеру..." -NoNewline
try {
    powercfg /change hibernate-timeout-ac 0
    Write-Host " OK" -ForegroundColor Green
} catch {
    Write-Host " (пропущено)" -ForegroundColor Yellow
}

# 5. Проверка текущих настроек
Write-Host "`nТекущие настройки:" -ForegroundColor Cyan
powercfg /query

Write-Host "`nГотово." -ForegroundColor Green
Write-Host "Примечание: для крышки ноутбука настройки можно изменить вручную:" -ForegroundColor Yellow
Write-Host "  Панель управления → Электропитание → Действия кнопок питания" -ForegroundColor Yellow
Write-Host "  Установите 'Закрытие крышки' → 'Действие не требуется'" -ForegroundColor Yellow
