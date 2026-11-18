@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 如果你的 Ollama 模型不在默认目录，改下面这一行
rem set OLLAMA_MODELS=D:\ollama\models
start "" pythonw app.py
