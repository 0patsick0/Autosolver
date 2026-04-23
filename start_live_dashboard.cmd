@echo off
setlocal
cd /d "%~dp0"
start "AutoSolver Live Dashboard" powershell -NoExit -ExecutionPolicy Bypass -File "%~dp0scripts\live_dashboard.ps1" %*
