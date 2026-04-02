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
token = json.loads(text)["access_token"]
status, text = call("GET", "/auth/session", headers={"Authorization": f"Bearer {token}"})
company_id = json.loads(text)["companies"][0]["id"]
headers = {"Authorization": f"Bearer {token}", "X-Client-Company-Id": str(company_id)}

status, text = call("GET", "/review/tasks/22/context", headers=headers)
print("context_status", status)
if status != 200:
    print(text)
    raise SystemExit(1)
obj = json.loads(text)
print("extraction", json.dumps(obj.get("extraction", {}), ensure_ascii=False, indent=2))
print("tax_lines", json.dumps(obj.get("tax_lines", []), ensure_ascii=False, indent=2))
