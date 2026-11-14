@echo off
rem Starts tools\voice_launcher.py with Windows (shortcut in the Startup folder) and right now. Run once by double-click.
rem Remove: delete "PiCar voice launcher.lnk" in shell:startup.
set PYTHONW=D:\a_ClaudeCowork\PiCar\voice\env\Scripts\pythonw.exe
set LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\PiCar voice launcher.lnk
powershell -NoProfile -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LINK); $s.TargetPath = $env:PYTHONW; $s.Arguments = '\"%~dp0voice_launcher.py\"'; $s.WorkingDirectory = '%~dp0'; $s.Save()"
start "" "%PYTHONW%" "%~dp0voice_launcher.py"
echo Installed: %LINK%
pause
