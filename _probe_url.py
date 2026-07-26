"""Probe: verify current_url() returns the real URL after browser_navigate."""
from src.browser_adapters.playwright_mcp_transport import PlaywrightMcpStdioSession
from src.browser_adapters.playwright_mcp_adapter import PlaywrightMcpBrowserAdapter
import time

print("Starting MCP...")
s = PlaywrightMcpStdioSession()
adapter = PlaywrightMcpBrowserAdapter(s)

print("Navigating to linkedin.com...")
t0 = time.time()
adapter.get("https://www.linkedin.com")
print(f"navigate done in {time.time()-t0:.1f}s")

print("Getting current_url...")
t1 = time.time()
url = adapter.current_url()
print(f"current_url in {time.time()-t1:.1f}s: '{url}'")

# Test is_logged_in logic
keywords = ['feed', 'mynetwork', 'jobs', 'messaging', 'notifications']
is_logged_in = any(item in url for item in keywords) and 'linkedin.com' in url
print(f"is_logged_in: {is_logged_in}")

adapter.close()
print("Done")