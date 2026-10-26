@echo off
rem Optional: put a HaphazardTasks shortcut on your desktop that points at this folder.
cd /d "%~dp0"
powershell -NoProfile -Command ^
  "$d = [Environment]::GetFolderPath('Desktop');" ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'HaphazardTasks.lnk'));" ^
  "$s.TargetPath = (Join-Path '%~dp0' 'Start HaphazardTasks.bat');" ^
  "$s.WorkingDirectory = '%~dp0';" ^
  "$s.WindowStyle = 7;" ^
  "$s.IconLocation = (Join-Path '%~dp0' 'app\static\icon.ico');" ^
  "$s.Description = 'HaphazardTasks - local, encrypted task dashboard';" ^
  "$s.Save()"
if errorlevel 1 (
  echo Could not create the shortcut. Right-click "Start HaphazardTasks.bat" and choose Send to ^> Desktop instead.
) else (
  echo Shortcut created on your desktop.
)
pause
