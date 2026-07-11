@echo off
rem ATIS quote recorder — scheduled weekdays 09:10 IST; exits on holidays/close.
cd /d D:\Developer\Projects\Atis
"C:\Users\Idhanth Karthik\.local\bin\uv.exe" run atis record >> data\recorder.log 2>&1
