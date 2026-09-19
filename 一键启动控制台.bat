@echo off
chcp 65001 >nul
title 手机端流程控制台
cd /d "%~dp0"
echo ======================================================
echo       手机端自动化运维监控控制台 (Mobile Console)
echo ======================================================
python launch_web.py
if errorlevel 1 pause
