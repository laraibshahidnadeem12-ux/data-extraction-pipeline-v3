@echo off
net session >nul 2>&1
if errorlevel 1 (
  echo NOT RUNNING AS ADMINISTRATOR.
  echo Right-click this file and choose "Run as administrator".
  pause
  exit /b 1
)
findstr /i "ocr.local" C:\Windows\System32\drivers\etc\hosts >nul 2>&1
if errorlevel 1 (
  echo 127.0.0.1 ocr.local>> C:\Windows\System32\drivers\etc\hosts
  echo Added: 127.0.0.1 ocr.local
) else (
  echo ocr.local is already in the hosts file.
)
echo.
echo Done. Start the server (python app.py) and open http://ocr.local
pause