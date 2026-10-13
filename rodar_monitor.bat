@echo off
REM ============================================================
REM Executa um comando do monitor Umbler e grava o resultado em log.
REM Uso: rodar_monitor.bat fila 120   |   rodar_monitor.bat canais
REM Chamado pelo Agendador de Tarefas do Windows (nao precisa abrir manualmente).
REM ============================================================
cd /d "%~dp0"
python monitor_umbler.py %* >> saida_monitor.txt 2>&1
exit /b %ERRORLEVEL%
