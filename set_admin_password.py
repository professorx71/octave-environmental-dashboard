from pathlib import Path
import getpass, hashlib, secrets, base64, re, json

BASE=Path(__file__).parent
ENV=BASE/".env"
if not ENV.exists():
    print("ERROR: .env not found. Copy your working .env into this folder first.")
    raise SystemExit(1)

username=input("Admin username [admin]: ").strip() or "admin"
if len(username) > 64 or any((not ch.isprintable()) or ch in "\r\n" for ch in username):
    print("ERROR: Username must be 1-64 printable characters. Letters, numbers, spaces and symbols are allowed.")
    raise SystemExit(1)
while True:
    p1=getpass.getpass("New admin password: ")
    p2=getpass.getpass("Confirm password: ")
    if p1 != p2:
        print("Passwords do not match.")
        continue
    if not re.fullmatch(r"[A-Za-z0-9]{6}", p1):
        print("Use exactly 6 characters: letters and numbers only.")
        continue
    break

iterations=310000
salt=secrets.token_bytes(18)
digest=hashlib.pbkdf2_hmac("sha256",p1.encode("utf-8"),salt,iterations)
encoded="pbkdf2_sha256${}${}${}".format(
    iterations,
    base64.urlsafe_b64encode(salt).decode("ascii"),
    base64.urlsafe_b64encode(digest).decode("ascii")
)

lines=ENV.read_text(encoding="utf-8").splitlines()
out=[]
seen_user=False
seen_hash=False
for line in lines:
    if line.startswith("ADMIN_USERNAME="):
        out.append("ADMIN_USERNAME="+json.dumps(username,ensure_ascii=False)); seen_user=True
    elif line.startswith("ADMIN_PASSWORD_HASH="):
        out.append("ADMIN_PASSWORD_HASH=\'"+encoded+"\'"); seen_hash=True
    else:
        out.append(line)
if not seen_user:
    out.append("ADMIN_USERNAME="+json.dumps(username,ensure_ascii=False))
if not seen_hash:
    out.append("ADMIN_PASSWORD_HASH=\'"+encoded+"\'")

ENV.write_text("\n".join(out)+"\n",encoding="utf-8")
print("")
print("Admin credentials configured successfully.")
print("Username:",username)
print("Restart the PBF dashboard service before logging in.")
