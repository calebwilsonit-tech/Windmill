@echo off
rem The `windmill` command (Windows). Beats windmill.py when this folder is on PATH.
where python >nul 2>nul && (python "%~dp0windmill.py" %* & exit /b) 
py -3 "%~dp0windmill.py" %*
