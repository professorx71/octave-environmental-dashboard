from pathlib import Path
from dotenv import dotenv_values

ENV=Path(__file__).parent/".env"
if not ENV.exists():
    print("ERROR: .env not found")
    raise SystemExit(1)

vals=dotenv_values(ENV, interpolate=False)

def hash_status(name):
    v=(vals.get(name) or "").strip()
    parts=v.split("$",3)
    ok=(
        len(parts)==4
        and parts[0]=="pbkdf2_sha256"
        and parts[1].isdigit()
        and bool(parts[2])
        and bool(parts[3])
    )
    return f"configured={'YES' if bool(v) else 'NO'}, format={'OK' if ok else 'BAD'}"

print("PBF Settings Authentication Check")
print("ADMIN_USERNAME:", vals.get("ADMIN_USERNAME") or "admin")
print("ADMIN_PASSWORD_HASH:", hash_status("ADMIN_PASSWORD_HASH"))
print("OPERATOR_USERNAME:", vals.get("OPERATOR_USERNAME") or "operator")
print("OPERATOR_PASSWORD_HASH:", hash_status("OPERATOR_PASSWORD_HASH"))
print("DEPLOYMENT_ROLE:", vals.get("DEPLOYMENT_ROLE") or "")
print("NOTIFICATIONS_ENABLED:", vals.get("NOTIFICATIONS_ENABLED") or "")
print("")
print("No passwords or secret hashes were displayed.")
