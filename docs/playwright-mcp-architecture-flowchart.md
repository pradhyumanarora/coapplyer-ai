# Playwright MCP Architecture & Decision Flowchart

## 1. Runtime Selection (main.py)

```
python main.py [--selenium] [--demo]
         │
         ▼
  resolve_browser_engine()
         │
    ┌────┴────────────────────────────┐
    │  --selenium flag passed?        │
    └────┬─────────────────────┬──────┘
       YES                    NO
         │                     │
         ▼                     ▼
   engine = "selenium"   engine = BROWSER_ENGINE
                         (from app_config.py,
                          default = "playwright")
         │                     │
         └────────┬────────────┘
                  ▼
        create_browser_runtime(engine)
```

---

## 2. Browser Runtime Creation (main.py)

```
create_browser_runtime(engine)
         │
    ┌────┴──────────────────────────────────┐
    │  engine == "selenium"?                │
    └────┬──────────────────────────┬───────┘
       YES                         NO (playwright)
         │                          │
         ▼                          ▼
   init_browser()            init_browser(cdp_port=9222)
   [no CDP port]             [launches Chrome with
         │                    --remote-debugging-port=9222]
         │                          │
         ▼                          ▼
  create_browser_adapter     create_browser_adapter(
  ("selenium",                 "playwright",
   selenium_driver=browser)    cdp_endpoint=
         │                     "http://localhost:9222")
         │                          │
    ┌────┴──────────────────────────┴────────┐
    │           Exception?                   │
    └────┬─────────────────────────┬─────────┘
       NO                        YES (FileNotFoundError/
         │                       TimeoutError/OSError)
         │                          │
         ▼                          ▼
  return browser,          log warning, fall back:
  browser_adapter          init_browser() + selenium
                                     │
                                     ▼
                            return browser,
                            SeleniumBrowserAdapter
```

**AI decision**: After studying ApplyPilot's `launcher.py`, the CDP pattern was chosen over direct stdio because Windows `.cmd` wrappers break subprocess stdio piping (node spawned as grandchild, pipe connects to cmd.exe not node).

---

## 3. Browser Adapter Factory (factory.py)

```
create_browser_adapter(engine, selenium_driver, cdp_endpoint)
         │
    ┌────┴──────────────────────────────────────┐
    │  engine                                   │
    └────┬────────────────────────┬─────────────┘
   "selenium"               "playwright"
         │                        │
         ▼                        ▼
  SeleniumBrowserAdapter   PlaywrightMcpStdioSession(
  (selenium_driver)          cdp_endpoint=cdp_endpoint)
                                   │
                                   ▼
                           PlaywrightMcpBrowserAdapter
                           (session)
```

---

## 4. Playwright MCP Session Startup (playwright_mcp_transport.py)

```
PlaywrightMcpStdioSession(cdp_endpoint="http://localhost:9222")
         │
         ▼
  _resolve_launch(command=None, args=None, cdp_endpoint, log)
         │
    ┌────┴──────────────────────────────────────────────┐
    │  cdp_endpoint provided?                           │
    └────┬──────────────────────────────────┬───────────┘
       YES                                 NO
         │                                  │
         ▼                                  ▼
  server_args =                      server_args = ["--headless"]
  ["--cdp-endpoint=http://           (let MCP manage its own browser)
    localhost:9222"]
         │                                  │
         └────────────┬─────────────────────┘
                      ▼
    Scan _NPX_STRATEGIES in order:
    1. npx.cmd @playwright/mcp  ← Windows .cmd wrapper (works for npx
    2. npx @playwright/mcp         because npx finds cached package)
    3. npx --yes @playwright/mcp@latest (downloads if not cached)
         │
    ┌────┴──────────────────────────────────────────────┐
    │  First strategy with command on PATH wins         │
    └────┬──────────────────────────────────────────────┘
         │
         ▼
  JsonRpcStdioClient.start()
    subprocess.Popen([npx.cmd, @playwright/mcp,
                      --cdp-endpoint=http://localhost:9222])
         │
         ▼
  Send JSON-RPC initialize directly (NOT via _request_internal
  to avoid re-entrant lock deadlock):
    Content-Length: N\r\n\r\n
    {"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
         │
         ▼
  Poll _messages queue up to startup_timeout_seconds (120s)
  waiting for response id=1
         │
    ┌────┴──────────────────────────────────────────────┐
    │  Response received?                               │
    └────┬──────────────────────────────────┬───────────┘
       YES                                 NO (timeout)
         │                                  │
         ▼                                  ▼
  notify("initialized", {})          close() + raise TimeoutError
  load tool schemas                  → main.py falls back to Selenium
```

**AI decision**: Re-entrant lock deadlock was identified by reading the call chain: `start()` → `_request_internal()` → `self.start()` (guard check) → acquires `_lock` → blocks because `_lock` already held by outer `_request_internal`. Fixed by sending initialize directly without going through `_request_internal`.

---

## 5. Why CDP over Stdio-Only

```
Windows subprocess stdio problem:
─────────────────────────────────

Python subprocess.Popen
    │ stdin/stdout PIPE
    ▼
cmd.exe (playwright-mcp.cmd or npx.cmd)
    │ child process (no pipe)
    ▼
node.exe → writes to its own stdout
    ✗ Python never receives node's output

CDP solution:
─────────────

Chrome (Selenium)  ←────────────────────────┐
  --remote-debugging-port=9222               │
  [user session, cookies, profile]           │
                                             │ WebSocket CDP
npx.cmd @playwright/mcp                      │
  --cdp-endpoint=http://localhost:9222 ──────┘
    │ JSON-RPC stdio (small, fast, reliable)
    ▼
Python JsonRpcStdioClient
  (initialize response comes in < 1s because
   browser is already running)
```

---

## 6. Browser Adapter Interface (BrowserAdapter ABC)

```
BrowserAdapter (base.py)
       │
  ┌────┴────────────────────────┐
  │                             │
SeleniumBrowserAdapter    PlaywrightMcpBrowserAdapter
(selenium_adapter.py)     (playwright_mcp_adapter.py)
       │                             │
  Uses raw Selenium            Uses PlaywrightMcpStdioSession
  WebDriver API                via JSON-RPC tool calls
       │                             │
       └────────────┬────────────────┘
                    │
          Both implement identical
          contract used by:
          - LinkedInAuthenticator
          - CoApplyerAIJobManager
          - CoApplyerAIEasyApplier
```

---

## 7. Per-Element Dispatch in EasyApplier

```
Any element interaction in linkedIn_easy_applier.py
         │
    ┌────┴──────────────────────────────────────────────┐
    │  self.browser (BrowserAdapter)                    │
    └────┬──────────────────────────────────────────────┘
         │
    ┌────┴────────────────────────────┐
    │  Is element PlaywrightMcpLocator│
    │  or raw Selenium WebElement?    │
    └────┬─────────────────────┬──────┘
   Locator                  WebElement
         │                     │
   uses JS via              uses Selenium
   evaluate()               native APIs
         │                     │
         └──────────┬──────────┘
                    ▼
              Result returned
              to applier logic

Special cases:
- _sdui_fill_geo: driver=None guard → JS dispatch for Playwright
- _handle_upload_fields: hasattr(element, "send_keys") guard
  → fill_text() for PlaywrightMcpLocator
- send_keys on locator: file inputs use JS value setter,
  text inputs use fill()
```

---

## 8. AI Reasoning Trace

| Decision Point | Evidence Used | Conclusion |
|---|---|---|
| Why MCP timed out | `stderr: no diagnostics`, `exit=None`, raw wire test returned `b''` | Process alive but not writing to stdout |
| Why zero stdout | `.cmd` file content showed `cmd.exe → node` chain; subprocess pipe connected to cmd.exe | Grandchild stdio not piped |
| How to fix | Read ApplyPilot `launcher.py`: CDP pattern, `--cdp-endpoint` arg | Launch Chrome first, attach MCP via CDP |
| Why npx.cmd works | `shutil.which('npx.cmd')` resolves to `C:\Program Files\nodejs\npx.cmd`; npx finds cached package without launching a grandchild node | npx.cmd is a thin wrapper, not a pipe-breaking intermediary |
| Deadlock source | Traced call chain: `start()→_request_internal()→self.start()→_lock.acquire()` while `_lock` already held | Bypass `_request_internal` for init, send directly |
| Selenium leaks | Static analysis of `linkedIn_easy_applier.py`: `ActionChains(self.driver)` unconditional, `element.send_keys()` on raw locator | Guard with `if self.driver is not None` + `hasattr` checks |