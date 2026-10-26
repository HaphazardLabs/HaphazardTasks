@echo off
rem Start HaphazardTasks (if it is not already running) and open the dashboard.
rem Runs entirely from this folder. No installer and no admin rights needed.
cd /d "%~dp0"
start "" "%~dp0python\pythonw.exe" "%~dp0launch.py"
