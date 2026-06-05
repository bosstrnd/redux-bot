@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
echo Запуск TRND Redux бота... (для остановки нажми Ctrl+C)
python bot.py
pause
