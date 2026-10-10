@echo off
echo Stopping any existing instance...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq streamlit*" >nul 2>&1

echo Starting Options Screener in background...
set PYTHON=C:\Users\socia\AppData\Local\Python\pythoncore-3.14-64\python.exe
set APP=%~dp0app.py

start "OptionsScreener" /B %PYTHON% -m streamlit run "%APP%" --server.port 8501 --server.headless true > "%~dp0streamlit.log" 2>&1

timeout /t 5 /nobreak > nul
echo.
echo App is running at http://localhost:8501
echo Log file: %~dp0streamlit.log
echo.
start http://localhost:8501
