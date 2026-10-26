HaphazardTasks for Windows
==========================

Local, encrypted task dashboard. Runs only on this computer, at http://127.0.0.1:8796/.
Nothing is installed system-wide and no admin rights are needed.

Start
  1. Unzip this folder somewhere under your user profile, for example Documents\HaphazardTasks.
  2. Double-click "Start HaphazardTasks.bat". It opens the dashboard in Edge (or Chrome) as its own window.
  3. Optional: double-click "Create desktop shortcut.bat" for a desktop icon.

First run
  Choose a passphrase (12+ characters) and save the recovery key it shows you.
  The recovery key is the only way back in if you forget the passphrase.

Where your data lives
  %APPDATA%\HaphazardTasks\vault.htv       the encrypted vault (AES-256-GCM)
  %APPDATA%\HaphazardTasks\vault.htv.1-.5  previous versions, also encrypted
  %APPDATA%\HaphazardTasks\server.log      request paths and status codes only
  This vault is separate from any other computer. Move data between machines only with
  Settings > Export encrypted backup / Restore from a backup file.

Stop
  The vault locks itself after the auto-lock time (Settings, default 15 minutes).
  The background server stops 30 minutes after the vault locks.

Updating
  Replace this folder with the new version. Your vault in %APPDATA% is not touched.

Before you use it for work data
  Check your employer's policy on running unapproved software and on where work data may be stored.
  Turn on BitLocker: decrypted data in memory can be written to the page file or hibernation file.

If Windows SmartScreen or your antivirus asks about python\pythonw.exe: it is the official,
signed Python runtime from python.org. The app itself is plain Python source in the app folder.
