@echo off
echo Starting Options Screener...
set PATH=C:\Users\socia\AppData\Local\Python\pythoncore-3.14-64\Scripts;%PATH%
cd /d "%~dp0"
python -m streamlit run app.py --server.port 8501 --server.headless true
pause
