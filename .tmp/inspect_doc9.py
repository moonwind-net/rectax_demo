import json
import urllib.request
import urllib.error

base = "http://127.0.0.1:8000/api/v1"

def call(method, path, data=None, headers=None):
    hdr = {}
    if headers:
        hdr.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdr.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(base + path, method=method, data=body, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")

status, text = call("POST", "/auth/login", {"email": "admin@example.com", "password": "admintest123456"})
print("login_status", status)
if status != 200:
    print(text)
    raise SystemExit(1)

token = json.loads(text)["access_token"]
status, text = call("GET", "/auth/session", headers={"Authorization": f"Bearer {token}"})
print("session_status", status)
if status != 200:
    print(text)
    raise SystemExit(1)
companies = json.loads(text).get("companies", [])
company_id = companies[0]["id"]
headers = {"Authorization": f"Bearer {token}", "X-Client-Company-Id": str(company_id)}

status, text = call("GET", "/documents/9", headers=headers)
print("doc9_status", status)
print(text)

status, text = call("GET", "/review/tasks", headers=headers)
print("tasks_status", status)
print(text)
