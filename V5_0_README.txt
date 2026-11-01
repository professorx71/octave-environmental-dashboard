PBF LI-COR Temperature Monitoring Dashboard v5.0
Designed by Daniel Salazar

NEW IN v5.0
- Logger Communication Status is populated for all six LI-COR loggers.
- ONLINE: latest data age <= 5 minutes.
- STALE: latest data age > 5 and <= 15 minutes.
- OFFLINE: latest data age > 15 minutes or no recent data.
- Persistent server-side alarm event history stored in data/alarm_events.json.
- Event history survives browser refresh/close and dashboard server restart.
- Records alarm start, 5-minute reminders, acknowledgement and return-to-normal.
- Records Gmail and ntfy phone-push delivery state (QUEUED/SENT/FAILED/NOT CONFIGURED).
- ACKNOWLEDGE is server-side and persists while the alarm remains active.
- Clear Log clears the persistent event history only; it does not change alarm limits or notification settings.

INSTALL / UPDATE
1. Stop the old dashboard (close its Command Prompt window).
2. Extract this ZIP to a new folder.
3. Copy your existing private .env file from v4.9.2 into the new folder.
4. Double-click start_dashboard.bat.
5. Browse to http://localhost:8000

SECURITY
The real .env is intentionally excluded. Do not share LI-COR API token, Gmail App Password, or private ntfy topic.
