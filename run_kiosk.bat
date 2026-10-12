@echo off
title clinicView Patient Kiosk
echo ========================================================
echo        clinicView -- AI Patient Kiosk (Sarvam + Gemini)
echo ========================================================
echo.
echo Starting server on http://localhost:8000 ...
echo Press Ctrl+C anytime to stop.
echo.
cd /d "%~dp0backend"
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
