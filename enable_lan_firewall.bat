@echo off
echo Allowing PBF Dashboard TCP port 8000 on PRIVATE Windows networks.
echo Windows may ask for Administrator permission.
powershell -NoProfile -Command "Start-Process netsh -Verb RunAs -ArgumentList 'advfirewall firewall add rule name="PBF LI-COR Dashboard TCP 8000" dir=in action=allow protocol=TCP localport=8000 profile=private'"
echo.
echo If Windows displayed a permission prompt, approve it.
pause
