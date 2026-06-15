@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo [1/2] 必要なライブラリを確認しています...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    py -m pip install -q -r requirements.txt
    if errorlevel 1 (
        echo pip のインストールに失敗しました。
        pause
        exit /b 1
    )
    set PY=py
) else (
    set PY=python
)
echo [2/2] サーバーを起動します...
echo.
%PY% app.py
pause
