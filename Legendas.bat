@echo off
REM Abre a interface grafica (sem janela de console).
start "" pythonw "%~dp0gui.py" 2>nul
if errorlevel 1 start "" python "%~dp0gui.py"
