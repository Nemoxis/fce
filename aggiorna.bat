@echo off
rem Controlla il sito FCE, elabora i PDF nuovi (e quelli messi in pdf_inbox) e verifica i dati.
cd /d "%~dp0"
set PYTHONUTF8=1
call .venv\Scripts\activate.bat || (echo Esegui prima installa.bat & pause & exit /b 1)
python -m pipeline.update
python -m tests.test_orari
echo.
echo Report dettagliati in state\reports\
pause
