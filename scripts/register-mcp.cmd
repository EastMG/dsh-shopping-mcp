@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
set "UV_CACHE_DIR=%CD%\..\.uv-cache-probe"
set "UV_PYTHON=3.12"

echo ============================================================
echo   shopping-mcp  -^>  DSH profile registration
echo ============================================================
echo.
echo Target: %USERPROFILE%\.dsh\profiles\desktop\cordis.patch.yml
echo.

uv run --with pyyaml python scripts\register_mcp.py
if errorlevel 1 goto fail

echo.
echo   OK - entry written, YAML validated, backup created.
echo.
echo   Next: restart DSH, then ask the agent to call shopping_status.
echo.
pause
exit /b 0

:fail
echo.
echo   FAILED - see the message above.
echo   The profile file is backed up before any write, so nothing
echo   is left in a half-written state.
echo.
pause
exit /b 1
