@echo off
cd /d "%~dp0..\"

docker run --rm -v "%cd%\.env:/app/.env" --name=ratingrelay ratingrelay:latest
