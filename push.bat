@echo off
rem ============================================================
rem  weather-agent : one-click commit and push to GitHub
rem
rem  Usage:
rem    push.bat        interactive, asks for confirmation
rem    push.bat -y     skip confirmation, fully automatic
rem
rem  NOTE 1: this file must stay pure ASCII. cmd.exe decodes batch
rem  files with the active ANSI code page, so Chinese characters
rem  break the parser even after "chcp 65001". Chinese docs live
rem  in docs/GIT_CHEATSHEET.md and push.sh instead.
rem
rem  NOTE 2: no "find /c" or for/f over pipes here on purpose.
rem  Those block waiting on stdin in some shells. File size
rem  (%%~zF) is a reliable way to test "is there any output".
rem ============================================================

setlocal enableextensions enabledelayedexpansion
cd /d "%~dp0"

set "AUTO=0"
if /i "%~1"=="-y" set "AUTO=1"
if /i "%~1"=="--yes" set "AUTO=1"

echo ==========================================
echo   weather-agent : commit and push
echo ==========================================
echo.

rem ---------- Gate 0: repository check ----------
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Not a git repository. Aborted.
    goto :fail
)

rem ---------- Gate 1: collect changes ----------
set "STATUS_FILE=%TEMP%\wa_git_status.txt"
set "DANGER_FILE=%TEMP%\wa_git_danger.txt"
git status --porcelain > "%STATUS_FILE%" 2>nul

set "STATUS_SIZE=0"
for %%F in ("%STATUS_FILE%") do set "STATUS_SIZE=%%~zF"

if "%STATUS_SIZE%"=="0" (
    echo [INFO] No uncommitted changes in working tree.
    echo.
    echo Checking for unpushed commits...
    git push
    if errorlevel 1 (
        echo.
        echo [ERROR] Push failed: remote ahead or network issue.
        goto :fail
    )
    echo.
    echo [DONE] Already up to date.
    goto :done
)

echo Changes to be committed:
echo ------------------------------------------
type "%STATUS_FILE%"
echo ------------------------------------------
echo.

rem ---------- Gate 2: block sensitive files ----------
rem .env.example is a template and must be excluded, or it warns every time.
findstr /I /R /C:"\.env" /C:"\.db" /C:"\.sqlite3" /C:"\.exe" /C:"logs/" "%STATUS_FILE%" 2>nul | findstr /I /V /C:".env.example" > "%DANGER_FILE%" 2>nul

set "DANGER_SIZE=0"
for %%F in ("%DANGER_FILE%") do set "DANGER_SIZE=%%~zF"

if not "%DANGER_SIZE%"=="0" (
    echo ########################################################
    echo #  [BLOCKED] sensitive or generated files detected:
    echo ########################################################
    type "%DANGER_FILE%"
    echo.
    echo   .env holds API keys, *.db is runtime data, *.exe is build output.
    echo   Once pushed to GitHub they stay in history forever.
    echo.
    echo   Fix: make sure .gitignore covers them, then unstage:
    echo         git restore --staged ^<file^>
    echo.
    goto :fail
)
echo [OK] No sensitive files detected.
echo.

rem ---------- Gate 3: confirmation ----------
if "%AUTO%"=="1" goto :commit

set "CONFIRM="
set /p "CONFIRM=Commit all changes above? [y/N] "
if /i not "%CONFIRM%"=="y" (
    if /i not "%CONFIRM%"=="yes" (
        echo.
        echo [CANCELLED] Nothing was changed.
        goto :done
    )
)

:commit
echo.
set "MSG="
if "%AUTO%"=="1" goto :use_default
set /p "MSG=Commit message (Enter for default): "

:use_default
if "%MSG%"=="" set "MSG=chore: local changes"

rem ---------- Execute ----------
echo.
echo [1/3] Staging all changes...
git add -A
if errorlevel 1 goto :fail

echo [2/3] Creating local commit...
git commit -m "%MSG%"
if errorlevel 1 (
    echo.
    echo [ERROR] Commit failed. If it says "nothing to commit",
    echo         the changes were already handled.
    goto :fail
)

echo [3/3] Pushing to remote...
git push
if not errorlevel 1 goto :push_ok

echo.
echo [INFO] Push rejected, remote has new commits. Trying rebase...
git pull --rebase origin main
if errorlevel 1 (
    echo.
    echo [ERROR] Automatic merge failed: code conflicts.
    echo         Resolve them, then run: git rebase --continue
    echo         To abort instead:        git rebase --abort
    goto :fail
)
git push
if errorlevel 1 goto :fail

:push_ok
echo.
echo ==========================================
echo   [DONE] Pushed to GitHub
echo ==========================================
git log --oneline -1
echo.
echo CI status: https://github.com/stannardgg/weather-agent/actions
goto :done

:fail
echo.
echo [ABORTED] Flow did not finish. Follow the hints above.
echo           Paste this output to the assistant if you need help.
echo.
pause
exit /b 1

:done
echo.
if "%AUTO%"=="0" pause
exit /b 0
