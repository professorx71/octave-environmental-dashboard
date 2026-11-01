PBF Rise Baking Burnaby Dashboard v6.0.3

AUTHENTICATION FIX

Problem fixed:
Docker Compose was loading .env values into the container environment and the application was also reading the mounted .env file. Password hashes contain $ characters. Compose environment parsing can alter those hashes before Python starts, so a newly reset Admin/Operator password can be rejected even though set_admin_password.py/set_operator_password.py wrote the correct hash to .env.

Fixes in v6.0.3:
1. docker-compose.yml no longer imports .env with env_file.
2. .env remains bind-mounted at /app/.env.
3. server.py uses load_dotenv(..., override=True), making the actual mounted .env file authoritative.
4. Added check_auth_config.py to verify usernames and password-hash format without displaying secrets.
5. 6-character alphanumeric Admin/Operator rule remains unchanged.

IMPORTANT:
- This patch preserves your existing .env, data, LI-COR credentials, Gmail, ntfy, schedules, offsets, Caddy, and CLOUD-PRIMARY settings.
- Reset the Admin and Operator passwords again after applying this patch, then recreate the dashboard container.
