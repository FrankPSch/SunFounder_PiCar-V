@echo off
rem Starts the PC half of the car's Claude conversation. Leave this window open while talking to the car.
title PiCar voice server
"D:\a_ClaudeCowork\PiCar\voice\env\Scripts\python.exe" -u "%~dp0voice_server.py"
pause
