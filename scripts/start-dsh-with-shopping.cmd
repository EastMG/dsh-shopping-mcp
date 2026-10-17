@echo off
rem ===========================================================================
rem  Boot DSH with shopping-mcp attached as a --patch overlay.
rem
rem  This writes NOTHING to ~/.dsh, so it works even when the profile
rem  directory is not writable (sandbox / low integrity level / locked ACL).
rem
rem  Trade-off: the MCP server is only present when DSH is started this way.
rem  If you want it always on, run register-mcp.cmd from a normal PowerShell
rem  window instead.
rem ===========================================================================
setlocal
set "PATCH=F:\DeepSeek\DeepSeek-Workplace\DSH-Workplace-01\shopping-mcp\dsh\mcp-shopping.patch.yml"

if not exist "%PATCH%" (
  echo Patch file not found: %PATCH%
  pause
  exit /b 1
)

echo Booting DSH with overlay:
echo   %PATCH%
echo.
dsh --profile desktop --patch "%PATCH%" web

echo.
echo DSH exited with code %ERRORLEVEL%.
pause
