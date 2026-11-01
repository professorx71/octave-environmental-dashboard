PBF LI-COR LIVE DASHBOARD v3

ALARM LIMITS
T1      HIGH > 13 C
T2      HIGH > 13 C
T3      HIGH > 13 C
Bagel   HIGH > 9 C
BP1     LOW < 30 C
BP2     LOW < 30 C

V3 UPGRADES
- Large active-alarm banner
- Alarm acknowledge button (visual acknowledgement only)
- Alarm transition log while dashboard browser is open
- Offline/stale logger count and status (over 5 minutes)
- Celsius/Fahrenheit display toggle
- Full-screen / TV display button
- 1-hour, 24-hour and 7-day temperature trends
- Alarm limit line on trend graph
- Automatic refresh every 60 seconds
- LI-COR token remains server-side

IMPORTANT
The acknowledge function only acknowledges the dashboard visual banner.
It does NOT acknowledge an alarm in LI-COR Cloud and does not replace a validated plant alarm/notification system.

UPDATE FROM V2
1. Close the old dashboard Command Prompt.
2. Extract this ZIP to C:\PBF_LICOR_LIVE_Dashboard_v3
3. Copy your existing private .env file from v2 into this v3 folder.
4. Do NOT send/share the .env file.
5. Double-click start_dashboard.bat
6. Open http://localhost:8000
7. Press Ctrl+F5 once if your browser shows an older cached page.

You already installed the Python packages, so normally no pip install is needed.

V3.1 UPDATE
- All six loggers now have their own temperature trend visible at the same time.
- Global 1 Hour / 24 Hours / 7 Days buttons update all six charts.
- Clicking a logger card scrolls to that logger's trend.

V3.2 ALARM ACKNOWLEDGE LOGIC
- Active unacknowledged alarm: flashing RED banner.
- Press ACKNOWLEDGE: banner becomes steady ORANGE.
- Text shows: ALARM ACKNOWLEDGED — CONDITION STILL ACTIVE.
- The alarm remains visible while the temperature remains outside its limit.
- When temperature returns within its limit, the banner clears automatically.
- A new alarm condition starts unacknowledged again.
- Acknowledge is local to the dashboard browser and does not acknowledge LI-COR Cloud.

V4 GMAIL ALERTS
- Test Email button.
- One email when an alarm starts.
- One recovery email when temperature returns to normal.
- No repeated email every refresh while the same alarm stays active.
- If dashboard restarts while an alarm is already active, one alarm email is sent.
- Gmail credentials remain in the private .env file.

V4.1 TEMPERATURE + HUMIDITY TRENDS / 24-HOUR DATA DOWNLOAD
- Every logger now displays a Temperature trend and Relative Humidity (%RH) trend.
- 1 Hour, 24 Hours, and 7 Days controls update both trend types.
- Each logger has Download 24h Temp + RH CSV.
- Download All 24h CSV downloads one CSV per logger.
- CSV contains Timestamp UTC, Temperature (C), and Humidity (%RH).
- Gmail alarm/recovery functions from v4 are retained.

V4.2 FIX
- Fixed Internal Server Error on 24-hour CSV download.
- Download now uses the same verified LI-COR getdata() function as the dashboard/history.
- Restored/verified Gmail configuration and alarm/recovery notification functions.
- Temperature and humidity trends remain included.

V4.3 RELIABLE 24-HOUR DOWNLOAD
- 24-hour download is now fetched as four 6-hour LI-COR API requests to avoid large-request timeout/server errors.
- Added missing asyncio import for Gmail Test Email.
- Added /api/download-test/{serial} diagnostic endpoint.
- CSV still contains Timestamp UTC, Temperature (C), Humidity (%RH).

V4.4 REPORT FORMAT
- 24-hour CSV timestamps converted from UTC to Pacific Time using America/Vancouver.
- Automatically handles PST/PDT daylight-saving changes.
- Separate Pacific Date and Pacific Time columns.
- Temperature rounded to 2 decimal places.
- Humidity rounded to 2 decimal places.

V4.5 PACIFIC TIME WINDOWS FIX
- Adds tzdata package required by Python/ZoneInfo on some Windows installations.
- start_dashboard.bat automatically checks America/Vancouver timezone support and installs tzdata if missing.
- Keeps Pacific date/time and 2-decimal temperature/humidity CSV formatting.

V4.6
- Adds 24-hour Min / Max / Average for Temperature and Humidity to each CSV.
- Adds server-side 60-second alarm monitor. Email alarms now continue while the dashboard browser is closed, as long as start_dashboard.bat/server remains running.
- Adds Test T3 Alarm Email button.
- Adds Email Status button showing configuration, last successful send, subject, and last SMTP error.
- Alarm/recovery emails remain transition-based: one on alarm start, one on recovery.

V4.7 FIVE-MINUTE ALARM EMAIL REMINDERS
- Initial email is sent immediately when an alarm is detected.
- While the alarm remains active, a reminder email is sent every 5 minutes.
- Reminder includes current temperature and total active alarm time.
- When the temperature returns to normal, reminders stop and one recovery email is sent.
- Server checks LI-COR every 60 seconds; start_dashboard.bat must remain running.
