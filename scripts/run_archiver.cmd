@echo off
rem ATIS intraday archiver — scheduled weekdays 15:40 IST, after market close.
cd /d D:\Developer\Projects\Atis
"C:\Users\Idhanth Karthik\.local\bin\uv.exe" run atis archive-intraday >> data\archiver.log 2>&1
