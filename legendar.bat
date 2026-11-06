@echo off
REM Arraste um video/audio em cima deste arquivo para gerar o SRT estilo TikTok.
setlocal
if "%~1"=="" (
  echo Arraste um video ou audio em cima deste .bat
  pause
  exit /b 1
)
python "%~dp0make_srt.py" "%~1" -w 2 --upper --tight
if errorlevel 1 (
  echo.
  echo Falhou. Veja a mensagem acima.
) else (
  echo.
  echo SRT gerado ao lado do arquivo original.
)
pause
