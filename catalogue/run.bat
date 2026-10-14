@echo off
rem Weekly PL catalogue update - target this file in Task Scheduler:
rem schtasks /Create /TN "pl-catalogue" /SC WEEKLY /D TUE /ST 02:00 /TR "C:\Users\User\Documents\Default Project\catalogue\run.bat"
"C:\Users\User\AppData\Local\Python\pythoncore-3.14-64\python.exe" "%~dp0run_weekly.py"
exit /b %ERRORLEVEL%
