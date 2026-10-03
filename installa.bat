@echo off
rem Prima installazione: crea l'ambiente Python e installa le librerie.
cd /d "%~dp0"
where python >nul 2>nul || (echo Python non trovato. Installalo da https://www.python.org/downloads/ spuntando "Add python.exe to PATH". & pause & exit /b 1)
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Installazione completata. Ora puoi usare aggiorna.bat e avvia_app.bat
pause
