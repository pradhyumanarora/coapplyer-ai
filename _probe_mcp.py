"""Diagnostic: test playwright-mcp headed mode SSE response timing + socket timeout fix."""
import socket
import time
import queue
import threading
import urllib.request
import json
import shutil
import subprocess

PORT = 3009
npx = shutil.which("npx.cmd") or shutil.which("npx")

# Kill any process on port 3009
try:
    r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=5)
    for line in r.stdout.splitlines():
        if f":{PORT} " in line and "LISTENING" in line:
            pid = line.split()[-1]
            if pid.isdigit():
                subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=5)
except Exception:
    pass
time.sleep(0.5)

# Start WITHOUT --headless
proc = subprocess.Popen(
    [npx, "@playwright/mcp", f"--port={PORT}"],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
print(f"Server started on port {PORT}")

base = f"http://localhost:{PORT}"
sse_conn = None
for _ in range(30):
    try:
        sse_conn = urllib.request.urlopen(
            urllib.request.Request(base + "/sse", headers={"Accept": "text/event-stream"}),
            timeout=5,
        )
        break
    except Exception:
        time.sleep(0.5)

if sse_conn is None:
    print("FAILED: could not connect to SSE")
    proc.terminate()
    exit(1)

# CRITICAL FIX: remove socket read timeout so long browser_navigate calls don't time out
raw_sock = None
try:
    raw_sock = sse_conn.fp.raw._sock
    raw_sock.settimeout(None)
    print(f"Socket timeout cleared (was: {raw_sock.gettimeout()})")
except AttributeError:
    # Try alternate path
    try:
        raw_sock = sse_conn.fp.fp.raw._sock
        raw_sock.settimeout(None)
        print("Socket timeout cleared (alt path)")
    except Exception as e:
        print(f"Could not clear socket timeout: {e}")

session_path = None
for raw in sse_conn:
    l = raw.decode().strip()
    if l.startswith("data:") and "sessionId" in l:
        session_path = l[5:].strip()
        break
print("session:", session_path)

resp_q: queue.Queue = queue.Queue()


def pump():
    event_type = "message"
    data_lines = []
    try:
        for raw in sse_conn:
            l = raw.decode().rstrip("\n\r")
            if not l:
                if data_lines and event_type == "message":
                    data = "\n".join(data_lines).strip()
                    if data:
                        try:
                            resp_q.put(json.loads(data))
                        except Exception:
                            pass
                event_type = "message"
                data_lines = []
            elif l.startswith("event:"):
                event_type = l[6:].strip()
            elif l.startswith("data:"):
                data_lines.append(l[5:].strip())
    except Exception as e:
        print(f"SSE pump died: {e}")


threading.Thread(target=pump, daemon=True).start()


def rpc(method, params, rid=None):
    payload = {"jsonrpc": "2.0", "method": method, "params": params}
    if rid is not None:
        payload["id"] = rid
    p = json.dumps(payload).encode()
    req = urllib.request.Request(
        base + session_path,
        data=p,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10).close()


# Initialize
rpc("initialize", {
    "protocolVersion": "2024-11-05",
    "clientInfo": {"name": "probe", "version": "0.1"},
    "capabilities": {},
}, rid=1)
msg = resp_q.get(timeout=10)
print("init:", msg.get("result", {}).get("serverInfo", {}).get("name"))
rpc("notifications/initialized", {})

# Test about:blank
print("\nNavigating to about:blank...")
t0 = time.time()
rpc("tools/call", {"name": "browser_navigate", "arguments": {"url": "about:blank"}}, rid=2)
try:
    r = resp_q.get(timeout=15)
    print(f"about:blank OK in {time.time()-t0:.2f}s")
except queue.Empty:
    print("TIMEOUT on about:blank!")

# Test linkedin.com
print("\nNavigating to linkedin.com (60s timeout)...")
t1 = time.time()
rpc("tools/call", {"name": "browser_navigate", "arguments": {"url": "https://www.linkedin.com"}}, rid=3)
try:
    r = resp_q.get(timeout=60)
    print(f"linkedin.com OK in {time.time()-t1:.2f}s  error={'error' in r}")
    content = r.get("result", {}).get("content", [])
    if content:
        print("content:", str(content[0])[:120])
except queue.Empty:
    print(f"TIMEOUT on linkedin after {time.time()-t1:.1f}s — socket timeout fix did NOT work")

proc.terminate()
print("\nDone.")