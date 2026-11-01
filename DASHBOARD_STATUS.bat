@echo off
echo ============================================================
echo PBF Temperature Dashboard Status
echo ============================================================
schtasks /query /TN "PBF Temperature Dashboard" /V /FO LIST
echo.
powershell.exe -NoProfile -Command "try {$r=Invoke-WebRequest http://127.0.0.1:8000/ -UseBasicParsing -TimeoutSec 5; Write-Host ('Dashboard responding - HTTP ' + $r.StatusCode) -ForegroundColor Green} catch {Write-Host ('Dashboard NOT responding: ' + $_.Exception.Message) -ForegroundColor Red}"
echo.
echo LAN addresses:
powershell.exe -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 ^| Where-Object {$_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*'} ^| ForEach-Object {Write-Host ('http://' + $_.IPAddress + ':8000')}"
pause
