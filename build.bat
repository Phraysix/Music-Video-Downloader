@echo off
chcp 65001 >nul
setlocal

echo === Muzik Indirici - EXE olusturma ===

if not exist "bin\ffmpeg.exe" goto noffmpeg
if not exist "bin\ffprobe.exe" goto noffmpeg

python -m pip install --upgrade -r requirements.txt
if errorlevel 1 goto fail

set DENO_ARG=
if exist "bin\deno.exe" (
  set DENO_ARG=--add-binary "bin\deno.exe;."
  echo Deno bulundu, exe'ye eklenecek.
) else (
  echo UYARI: bin\deno.exe yok. Arkadaslarinin bilgisayarinda Deno kurulu degilse YouTube indirmeleri sorun cikarabilir.
)

set ICON_ARG=
if exist "icon.ico" (
  set ICON_ARG=--icon "icon.ico" --add-data "icon.ico;."
) else (
  echo UYARI: icon.ico bulunamadi, exe varsayilan ikonla olusturulacak.
)

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name MuzikIndirici ^
  --collect-all customtkinter ^
  --collect-all yt_dlp ^
  --collect-all yt_dlp_ejs ^
  --add-binary "bin\ffmpeg.exe;." ^
  --add-binary "bin\ffprobe.exe;." ^
  %DENO_ARG% ^
  %ICON_ARG% ^
  app.py
if errorlevel 1 goto fail

echo.
echo Bitti! EXE dosyasi: dist\MuzikIndirici.exe
pause
exit /b 0

:noffmpeg
echo.
echo HATA: bin klasorunde ffmpeg.exe ve ffprobe.exe bulunamadi.
echo Bkz. README.md
pause
exit /b 1

:fail
echo.
echo HATA: Derleme basarisiz oldu.
pause
exit /b 1
