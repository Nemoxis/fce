@echo off
setlocal
rem ------------------------------------------------------------------
rem  Crea una copia pulita del progetto, pronta da caricare su GitHub.
rem  Destinazione: Desktop\FCE_github
rem  Esclude: ambiente Python locale (.venv), file temporanei, cartella .git
rem ------------------------------------------------------------------
cd /d "%~dp0"
set "DEST=%USERPROFILE%\Desktop\FCE_github"

echo.
echo Preparo la cartella da caricare su GitHub:
echo   %DEST%
echo.

if exist "%DEST%" (
    echo La cartella esiste gia': la ricreo da zero.
    rmdir /s /q "%DEST%"
)

robocopy "%~dp0." "%DEST%" /E /NFL /NDL /NJH /NJS /NP ^
    /XD ".venv" "__pycache__" ".git" ".idea" ".vscode" ^
    /XF "*.pyc" "*.pyo" "*.log" "Thumbs.db" "desktop.ini"
if %ERRORLEVEL% GEQ 8 (
    echo.
    echo ERRORE durante la copia. Controlla i permessi della cartella.
    pause
    exit /b 1
)

rem Controllo dei file indispensabili
set "MANCA="
for %%F in (
    ".github\workflows\aggiorna-orari.yml"
    "requirements.txt"
    "pipeline\update.py"
    "docs\index.html"
    "docs\data\index.json"
    "config\sources.yaml"
) do (
    if not exist "%DEST%\%%~F" (
        echo MANCA: %%~F
        set "MANCA=1"
    )
)
if defined MANCA (
    echo.
    echo Alcuni file indispensabili mancano: la cartella non e' pronta.
    pause
    exit /b 1
)

echo Copia completata e controllata.
echo.
echo PROSSIMI PASSI
echo  1. Apri GitHub Desktop: File ^> Add local repository ^> scegli
echo     %DEST%
echo     (se ti chiede di creare il repository, conferma "create a repository").
echo  2. Premi "Publish repository" e TOGLI la spunta "Keep this code private".
echo  3. Su github.com, nel repository:
echo       Settings ^> Pages ^> Source = "GitHub Actions"
echo       Settings ^> Actions ^> General ^> Workflow permissions = "Read and write"
echo  4. Actions ^> "Aggiorna orari FCE" ^> Run workflow.
echo.
echo NOTA: non caricare i file trascinandoli nel sito di GitHub: la cartella
echo nascosta ".github" (quella dell'aggiornamento automatico) verrebbe saltata.
echo.

explorer "%DEST%"
pause
