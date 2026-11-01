PBF Rise Baking Burnaby Temperature Dashboard
v6.0.3 OVH Cloud Edition

ARCHITECTURE
LI-COR Cloud -> OVH VPS -> PBF Dashboard -> Gmail/ntfy
GMKtec mini-PC remains local backup.

SAFETY DEFAULT
NOTIFICATIONS_ENABLED=false
This prevents duplicate automatic alarms while the existing mini-PC remains active.
Manual dashboard testing can be completed before cloud notifications are enabled.

INITIAL DEPLOYMENT
1. Install Docker + Docker Compose on Ubuntu 24.04.
2. Upload this folder to /opt/pbf-temp-dashboard.
3. Create .env from .env.cloud.example and enter private credentials.
4. Run: ./deploy_cloud.sh
5. The FastAPI app binds only to 127.0.0.1:8000 on the VPS.
6. From Windows create an SSH tunnel:
   ssh -L 8000:127.0.0.1:8000 ubuntu@YOUR_VPS_IP
7. Open http://localhost:8000 and verify all seven loggers.

PUBLIC HTTPS
After IT/DNS gives you a domain/subdomain pointing to the VPS:
1. Run: ./enable_https.sh
2. Enter the domain, viewer username, and a strong viewer password.
3. Caddy automatically obtains and renews HTTPS certificates.
4. The whole dashboard is protected by Caddy basic authentication over HTTPS.
5. Settings still has separate Admin/Operator role protection.

GO-LIVE
Only after cloud tests pass:
- Disable automatic Gmail/ntfy alarms on the GMKtec backup.
- Change cloud .env to:
    NOTIFICATIONS_ENABLED=true
    DEPLOYMENT_ROLE=CLOUD-PRIMARY
- Restart: docker compose restart dashboard

DATA
Persistent alarm/config/event files are stored in ./data on the VPS and are included
in OVH server backups.
