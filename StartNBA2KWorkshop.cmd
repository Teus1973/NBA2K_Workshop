@echo off
setlocal
set "ROOT=%~dp0"
if exist "%ROOT%NBA2K Workshop.exe" start "" "%ROOT%NBA2K Workshop.exe" & exit /b 0
if exist "%ROOT%NBA2KWorkshop.exe" start "" "%ROOT%NBA2KWorkshop.exe" & exit /b 0
if exist "%ROOT%StartNBA2KWorkshop.exe" start "" "%ROOT%StartNBA2KWorkshop.exe" & exit /b 0
if exist "%ROOT%WorkshopApp.exe" start "" "%ROOT%WorkshopApp.exe" & exit /b 0
if exist "%ROOT%LaunchNBA2KWorkshop.exe" start "" "%ROOT%LaunchNBA2KWorkshop.exe" & exit /b 0
if exist "%ROOT%dist\StartNBA2KWorkshop.exe" start "" "%ROOT%dist\StartNBA2KWorkshop.exe" & exit /b 0
for /f "delims=" %%I in ('dir /b /ad /o-d "%ROOT%build\launcher_dist_*" 2^>nul') do (
  if exist "%ROOT%build\%%I\NBA2KWorkshop.exe" start "" "%ROOT%build\%%I\NBA2KWorkshop.exe" & exit /b 0
  if exist "%ROOT%build\%%I\LaunchNBA2KWorkshop.exe" start "" "%ROOT%build\%%I\LaunchNBA2KWorkshop.exe" & exit /b 0
)
echo No launcher .exe found. Build with:  scripts\build_workshop_launcher.ps1
pause
exit /b 1
