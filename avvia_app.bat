@echo off
rem Apre l'app nel browser del PC (http://localhost:8000). Chiudi questa finestra per fermarla.
cd /d "%~dp0"
call .venv\Scripts\activate.bat || (echo Esegui prima installa.bat & pause & exit /b 1)
start "" http://localhost:8000
python -m http.server 8000 --directory docs
