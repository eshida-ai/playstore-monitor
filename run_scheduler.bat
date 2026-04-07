@echo off
cd /d "%~dp0"
"C:\Users\eshida\AppData\Local\Programs\Python\Python314\python.exe" check_play_featured.py >> "%~dp0scheduler_log.txt" 2>&1
