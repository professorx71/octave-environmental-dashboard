PBF LI-COR Dashboard v5.0.1
Fixes the blank Logger Communication Status and Alarm Event Log panels.

Root fix:
- Explicitly binds the HTML elements in JavaScript instead of relying on browser-created global variables.
- Adds cache-busting to app.js so Chrome loads the corrected JavaScript.
- Keeps the v5 server-side persistent alarm log and communication status.
- Adds /api/v501-status diagnostic endpoint.

INSTALL:
1. Stop the old dashboard.
2. Extract this ZIP to a NEW folder.
3. Copy your private .env from your working v4.9.2/v5.0 folder into this folder.
4. Run start_dashboard.bat.
5. Open http://localhost:8000
6. Press Ctrl+F5 once to force-refresh the browser.

Do not share your .env file.
