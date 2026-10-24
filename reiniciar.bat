@echo off
rem Reinicia o MeMi BOT: avisa o bot que esta rodando (ele fecha com calma e abre de
rem novo). Se nao houver nenhum rodando, so abre. Pode clicar duas vezes.
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw memi_tray.py --reiniciar
) else (
    start "" pyw memi_tray.py --reiniciar
)
