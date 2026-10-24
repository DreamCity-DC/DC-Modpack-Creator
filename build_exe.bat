@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"
set "PYINSTALLER_CONFIG_DIR=%ROOT%.cache\pyinstaller"

echo Building DreamCity Modpack Creator...
uv run --locked --no-dev --group build pyinstaller ^
    --noconsole ^
    --onefile ^
    --clean ^
    --noconfirm ^
    --paths "%ROOT%src" ^
    --specpath "build\spec" ^
    --workpath "build\pyinstaller" ^
    --distpath "dist" ^
    --name "DreamCity整合包导出工具" ^
    --add-data "%ROOT%src\dc_modpack_creator\vendor;dc_modpack_creator\vendor" ^
    --add-data "%ROOT%src\dc_modpack_creator\assets;dc_modpack_creator\assets" ^
    main.py
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)
echo Build complete. See dist\DreamCity整合包导出工具.exe
pause
