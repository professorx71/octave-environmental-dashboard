PBF Rise Baking Burnaby Dashboard v6.0.0

Major updates:
- Alarm reminder frequency changed from every 5 minutes to every 30 minutes.
- Immediate alarm notification remains unchanged.
- Recovery notification still sends once when temperature returns to normal.
- Added password-protected Alarm Administration page.
- Admin can change each logger's temperature alarm setpoint.
- Admin can place equipment into NOT IN USE mode.
- NOT IN USE keeps temperature/humidity visible and logged but suppresses
  temperature alarm emails/push notifications to prevent false alarms when
  refrigeration is intentionally off.
- Every setpoint or monitoring-mode change is written to the Event Log.
- Added Windows trust-store support for ntfy HTTPS on corporate networks.
- Admin sessions expire after 8 hours.
- Communication thresholds remain:
  ONLINE <= 15 min, STALE 15-30 min, OFFLINE > 30 min.

ADMIN SETUP:
1. Copy your private .env into this folder.
2. Run SET_ADMIN_PASSWORD.bat on the mini-PC.
3. Choose an admin username/password.
4. Restart the PBF dashboard scheduled task.
5. Open http://<mini-pc-ip>:8000/settings

SECURITY NOTE:
Admin passwords are stored in .env as a PBKDF2-SHA256 hash, not plaintext.
The login itself uses HTTP on the plant LAN. For stronger security across
untrusted networks, add HTTPS/VPN. Never expose port 8000 directly to the Internet.
