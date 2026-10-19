"""ALB target for the temporary wake-page rule. Shows a friendly page and triggers the wake."""
import json, os, base64
import boto3
REGION = os.environ.get("AWS_REGION", "ap-south-1")
P = os.environ.get("PARAM_PREFIX", "/sybeez-school/idle-shutdown")
CONTROLLER = os.environ["CONTROLLER_FN"]
ssm = boto3.client("ssm", region_name=REGION); lam = boto3.client("lambda", region_name=REGION)

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30"><title>Sybeez is starting</title>
<style>body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f6f7fb;color:#1c2333;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
.c{background:#fff;border-radius:16px;padding:40px 48px;max-width:480px;text-align:center;box-shadow:0 6px 30px rgba(0,0,0,.08)}
.s{width:44px;height:44px;border:4px solid #dde1ee;border-top-color:#5b6cff;border-radius:50%;margin:0 auto 24px;animation:r 1s linear infinite}
@keyframes r{to{transform:rotate(360deg)}}h1{font-size:22px;margin:0 0 12px}p{margin:8px 0;color:#4b5468;line-height:1.5}</style></head>
<body><div class="c"><div class="s"></div><h1>Sybeez is waking up</h1>
<p>The platform is starting after a period of inactivity. This usually takes about 7&ndash;12 minutes.</p>
<p>This page refreshes automatically. You don&rsquo;t need to do anything.</p></div></body></html>"""

def _h(d, k):
    for a, b in d.items():
        if a.lower() == k: return b
    return ""

def handler(event, context):
    headers = event.get("headers") or {}; path = event.get("path", "/")
    origin = _h(headers, "origin"); referer = _h(headers, "referer")
    html = "text/html" in _h(headers, "accept") and _h(headers, "sec-fetch-mode") in ("navigate", "")
    user = (".sybeez.ai" in origin or ".sybeez.ai" in referer or origin.endswith("sybeez.ai")
            or bool(_h(headers, "authorization")) or (html and _h(headers, "sec-fetch-mode") == "navigate"))
    monitor = path in ("/health", "/healthz")
    woke = False
    if user and not monitor:
        try:
            st = ssm.get_parameter(Name=f"{P}/state")["Parameter"]["Value"]
            if st != "WAKING": ssm.put_parameter(Name=f"{P}/state", Value="WAKING", Type="String", Overwrite=True)
            lam.invoke(FunctionName=CONTROLLER, InvocationType="Event", Payload=json.dumps({"action": "wake"}).encode())
            woke = True
        except Exception as e: print(json.dumps({"event": "wake_trigger_error", "err": str(e)}))
    print(json.dumps({"event": "wake_page_hit", "path": path, "user": user, "woke": woke}))
    cors = {"Access-Control-Allow-Origin": origin if origin.endswith("sybeez.ai") else "https://school.sybeez.ai",
            "Access-Control-Allow-Credentials": "true", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*", "Vary": "Origin"}
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 204, "headers": cors, "body": "", "isBase64Encoded": False}
    base = {"Cache-Control": "no-store", "Retry-After": "30", **cors}
    if html:
        return {"statusCode": 503, "headers": {**base, "Content-Type": "text/html; charset=utf-8"}, "body": PAGE, "isBase64Encoded": False}
    return {"statusCode": 503, "headers": {**base, "Content-Type": "application/json"},
            "body": json.dumps({"status": "waking_up", "message": "Sybeez is waking up (about 7-12 minutes). Please retry shortly.", "retry_after_seconds": 30}),
            "isBase64Encoded": False}
