from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ToolSchema:
    name: str
    input_schema: dict[str, Any]


# ---------------------------------------------------------------------------
# HTTP/SSE MCP client — works on Windows without subprocess PIPE issues
# ---------------------------------------------------------------------------

class PlaywrightMcpSseClient:
    """JSON-RPC over HTTP+SSE transport for playwright-mcp --port <N>.

    Protocol (discovered by probing):
      1. GET /sse  →  event: endpoint\n  data: /sse?sessionId=<uuid>
      2. Keep GET /sse open (SSE stream) — responses come back here
      3. POST /sse?sessionId=<uuid>  with JSON-RPC payload  →  202 Accepted
      4. Response appears on the SSE stream as:  event: message\n  data: {...}
    """

    def __init__(
        self,
        port: int,
        *,
        timeout_seconds: int = 30,
    ):
        self.base_url = f"http://localhost:{port}"
        self.timeout_seconds = timeout_seconds
        self._request_id = 0
        self._send_lock = threading.Lock()
        self._session_path: str | None = None          # e.g. "/sse?sessionId=uuid"
        self._response_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._sse_thread: threading.Thread | None = None
        self._sse_response: urllib.request.Request | None = None
        self._sse_conn: Any = None

    def connect(self, startup_deadline: float) -> None:
        """Establish SSE session and start background reader thread.

        The SSE connection must NOT have a socket read timeout because
        browser tool calls (browser_navigate to LinkedIn etc.) can take
        30-90 seconds. We use a short timeout only for the initial connect
        attempt, then clear it to allow unbounded reads.
        """
        sse_req = urllib.request.Request(
            f"{self.base_url}/sse",
            headers={"Accept": "text/event-stream"},
        )
        while time.time() < startup_deadline:
            try:
                self._sse_conn = urllib.request.urlopen(sse_req, timeout=5)
                # CRITICAL: remove the socket read timeout after connecting.
                # urllib.request.urlopen(timeout=5) sets a socket-level timeout
                # that applies to EVERY subsequent read on the socket.
                # Long browser_navigate calls take 10-90s → socket times out
                # mid-stream, killing the SSE connection silently.
                if hasattr(self._sse_conn, "fp") and hasattr(self._sse_conn.fp, "raw"):
                    try:
                        self._sse_conn.fp.raw._sock.settimeout(None)
                    except Exception:
                        pass
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise TimeoutError("Could not connect to playwright-mcp SSE endpoint")

        # Read until we get the endpoint event with the sessionId
        for raw_line in self._sse_conn:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\n\r")
            if line.startswith("data:"):
                path = line[len("data:"):].strip()
                if "sessionId" in path:
                    self._session_path = path
                    break

        if not self._session_path:
            raise RuntimeError("playwright-mcp SSE server did not send sessionId")

        # Start background thread to read SSE messages
        self._sse_thread = threading.Thread(target=self._pump_sse, daemon=True)
        self._sse_thread.start()

    def _pump_sse(self) -> None:
        """Read SSE messages from the open connection into the response queue.

        SSE format:
            event: message      ← sets current event type
            data: {...}         ← payload
                                ← blank line resets state
        Per SSE spec, the default event type is "message" when event: is omitted.
        """
        if self._sse_conn is None:
            return
        event_type = "message"  # SSE default when event: line is absent
        data_lines: list[str] = []
        try:
            for raw_line in self._sse_conn:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n\r")
                if not line:
                    # Blank line = dispatch event
                    if data_lines and event_type == "message":
                        data = "\n".join(data_lines).strip()
                        if data:
                            try:
                                self._response_queue.put(json.loads(data))
                            except json.JSONDecodeError:
                                pass
                    # Reset
                    event_type = "message"
                    data_lines = []
                elif line.startswith("event:"):
                    event_type = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[len("data:"):].strip())
        except Exception:
            pass  # Connection closed on shutdown — expected

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a JSON-RPC request and wait for the response on the SSE stream."""
        with self._send_lock:
            self._request_id += 1
            rid = self._request_id

        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": rid,
            "method": method,
            "params": params or {},
        }).encode("utf-8")

        post_url = f"{self.base_url}{self._session_path}"
        req = urllib.request.Request(
            post_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10).close()

        # Wait for response on SSE stream
        deadline = time.time() + self.timeout_seconds
        while time.time() < deadline:
            remaining = max(0.1, deadline - time.time())
            try:
                msg = self._response_queue.get(timeout=min(remaining, 2.0))
                if msg.get("id") == rid:
                    if "error" in msg:
                        raise RuntimeError(f"MCP error: {msg['error']}")
                    return msg.get("result")
                # Put back if wrong id (shouldn't happen in serial usage)
                self._response_queue.put(msg)
            except queue.Empty:
                pass
        raise TimeoutError(f"Timed out waiting for MCP response to {method}")

    def notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self._session_path:
            return
        payload = json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }).encode("utf-8")
        post_url = f"{self.base_url}{self._session_path}"
        req = urllib.request.Request(
            post_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5).close()
        except Exception:
            pass

    def close(self) -> None:
        try:
            self.notify("shutdown", {})
        except Exception:
            pass
        if self._sse_conn is not None:
            try:
                self._sse_conn.close()
            except Exception:
                pass
            self._sse_conn = None


# ---------------------------------------------------------------------------
# stdio client (kept for non-Windows / reference)
# ---------------------------------------------------------------------------

class JsonRpcStdioClient:
    def __init__(
        self,
        command: str,
        args: Iterable[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
        startup_timeout_seconds: int = 180,
    ):
        self.command = command
        self.args = list(args)
        self._resolved_command = self._resolve_command(command)
        self.cwd = cwd
        self.env = env
        self.timeout_seconds = timeout_seconds
        self.startup_timeout_seconds = startup_timeout_seconds
        self._process: subprocess.Popen[bytes] | None = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr_lines: list[str] = []
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._starting: bool = False

    def start(self) -> None:
        if self._process is not None or self._starting:
            return
        self._starting = True
        try:
            self._process = subprocess.Popen(
                [self._resolved_command, *self.args],
                cwd=self.cwd,
                env=self.env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._start_reader_threads()
            init_params = {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "coapplyer-ai", "version": "0.1.0"},
                "capabilities": {},
            }

            with self._lock:
                self._request_id += 1
                init_request_id = self._request_id
                self._send_message({
                    "jsonrpc": "2.0",
                    "id": init_request_id,
                    "method": "initialize",
                    "params": init_params,
                })

            started = False
            startup_deadline = time.time() + self.startup_timeout_seconds
            while time.time() < startup_deadline:
                remaining = max(0.1, startup_deadline - time.time())
                try:
                    message = self._messages.get(timeout=min(remaining, 5.0))
                except queue.Empty:
                    continue
                if message.get("id") == init_request_id:
                    if "error" in message:
                        raise RuntimeError(f"MCP initialize error: {message['error']}")
                    started = True
                    break

            if not started:
                stderr_tail = " | ".join(self._stderr_lines[-10:])
                exit_code = self._process.poll() if self._process is not None else None
                self.close()
                raise TimeoutError(
                    f"Timed out waiting for MCP initialize (exit={exit_code}). details: {stderr_tail or 'no diagnostics'}"
                )

            self.notify("initialized", {})
            self._starting = False
        except FileNotFoundError as exc:
            self._starting = False
            raise RuntimeError(f"Unable to start MCP command '{self.command}'") from exc
        except Exception:
            self._starting = False
            raise

    def _start_reader_threads(self) -> None:
        if self._process is None:
            return
        self._stdout_thread = threading.Thread(target=self._pump_stdout, daemon=True)
        self._stdout_thread.start()
        if self._process.stderr is not None:
            self._stderr_thread = threading.Thread(target=self._pump_stderr, daemon=True)
            self._stderr_thread.start()

    def _pump_stdout(self) -> None:
        while self._process is not None and self._process.stdout is not None:
            message = self._read_message_from_stream(self._process.stdout)
            if message is None:
                return
            self._messages.put(message)

    def _pump_stderr(self) -> None:
        if self._process is None or self._process.stderr is None:
            return
        while True:
            line = self._process.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                self._stderr_lines.append(text)
                if len(self._stderr_lines) > 100:
                    self._stderr_lines = self._stderr_lines[-100:]

    def _resolve_command(self, command: str) -> str:
        if sys.platform == "win32" and not command.lower().endswith((".cmd", ".exe", ".bat")):
            resolved_cmd = shutil.which(command + ".cmd")
            if resolved_cmd:
                return resolved_cmd
        resolved = shutil.which(command)
        if resolved:
            return resolved
        return command

    def close(self) -> None:
        if self._process is None:
            return
        try:
            self.notify("shutdown", {})
        except Exception:
            pass
        try:
            if self._process.stdin:
                self._process.stdin.close()
        finally:
            self._process.terminate()
            self._process = None

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send_message({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        return self._request_internal(method, params, timeout_seconds=self.timeout_seconds)

    def _request_internal(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout_seconds: int | None = None,
        *,
        close_on_timeout: bool = True,
    ) -> Any:
        if not self._starting:
            self.start()
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            self._send_message({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
            deadline = time.time() + (timeout_seconds if timeout_seconds is not None else self.timeout_seconds)
            while time.time() < deadline:
                remaining = max(0.1, deadline - time.time())
                try:
                    message = self._messages.get(timeout=remaining)
                except queue.Empty:
                    break
                if message.get("id") == request_id:
                    if "error" in message:
                        raise RuntimeError(message["error"])
                    return message.get("result")

        stderr_tail = " | ".join(self._stderr_lines[-5:])
        if close_on_timeout:
            self.close()
        if stderr_tail:
            raise TimeoutError(f"Timed out waiting for MCP response to {method}. stderr: {stderr_tail}")
        raise TimeoutError(f"Timed out waiting for MCP response to {method}")

    def _send_message(self, payload: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("MCP process is not started")
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("utf-8")
        self._process.stdin.write(header + encoded)
        self._process.stdin.flush()

    def _read_message_from_stream(self, stream) -> dict[str, Any] | None:
        if self._process is None:
            raise RuntimeError("MCP process is not started")
        while True:
            line = stream.readline()
            if not line:
                return None
            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded:
                continue
            if not decoded.lower().startswith("content-length:"):
                self._stderr_lines.append(f"stdout: {decoded}")
                if len(self._stderr_lines) > 100:
                    self._stderr_lines = self._stderr_lines[-100:]
                continue
            try:
                content_length = int(decoded.split(":", 1)[1].strip())
            except ValueError:
                continue
            while True:
                header_line = stream.readline()
                if not header_line:
                    return None
                if header_line in (b"\r\n", b"\n"):
                    break
            body = stream.read(content_length)
            if not body:
                return None
            return json.loads(body.decode("utf-8"))


# ---------------------------------------------------------------------------
# PlaywrightMcpStdioSession — unified session using SSE on Windows, stdio elsewhere
# ---------------------------------------------------------------------------

class PlaywrightMcpStdioSession:
    """MCP session for @playwright/mcp.

    On Windows: starts playwright-mcp in HTTP/SSE mode (--port <N>) to avoid
    the .cmd wrapper subprocess PIPE buffering issue. Communicates via HTTP.

    On Unix/macOS: uses stdio transport directly (npx works fine).

    Recommended: pass cdp_endpoint so playwright-mcp attaches to an existing
    Chrome instead of launching its own browser.
    """

    _DEFAULT_SSE_PORT = 3001  # HTTP port for SSE mode on Windows

    # npx strategies for stdio (Unix) or HTTP startup (Windows)
    _NPX_STRATEGIES: list[tuple[str, list[str]]] = [
        ("npx.cmd", ["@playwright/mcp"]),
        ("npx", ["@playwright/mcp"]),
        ("npx", ["--yes", "@playwright/mcp@latest"]),
    ]

    def __init__(
        self,
        command: str | None = None,
        args: Iterable[str] | None = None,
        *,
        cdp_endpoint: str | None = None,
        extra_mcp_args: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 90,
        startup_timeout_seconds: int = 120,
        sse_port: int | None = None,
    ):
        import logging as _logging
        _log = _logging.getLogger(__name__)

        self._process: subprocess.Popen | None = None
        self._args = list(args) if args is not None else None
        self._extra_mcp_args: list[str] = list(extra_mcp_args) if extra_mcp_args else []

        if sys.platform == "win32" and command is None:
            # Windows: use HTTP/SSE mode to avoid subprocess PIPE issues
            port = sse_port or self._DEFAULT_SSE_PORT
            self.client = self._start_sse_mode(
                extra_server_args=self._extra_mcp_args,
                cdp_endpoint=cdp_endpoint,
                port=port,
                startup_timeout_seconds=startup_timeout_seconds,
                timeout_seconds=timeout_seconds,
                log=_log,
            )
        else:
            # Unix/macOS or explicit command override: use stdio
            resolved_command, resolved_args = self._resolve_stdio_launch(
                command, args, cdp_endpoint, _log
            )
            _log.info("Starting Playwright MCP (stdio): %s %s", resolved_command, " ".join(resolved_args))
            stdio_client = JsonRpcStdioClient(
                resolved_command,
                resolved_args,
                cwd=cwd,
                env=env,
                timeout_seconds=timeout_seconds,
                startup_timeout_seconds=startup_timeout_seconds,
            )
            stdio_client.start()
            self.client = stdio_client

        _log.info("Playwright MCP server ready.")
        self.tool_schemas: dict[str, ToolSchema] = self._load_tool_schemas()

    @staticmethod
    def _kill_port(port: int) -> None:
        """Kill any process listening on the given port (Windows + Unix)."""
        try:
            if sys.platform == "win32":
                # netstat -ano to find PID, then taskkill
                result = subprocess.run(
                    ["netstat", "-ano"],
                    capture_output=True, text=True, timeout=5
                )
                for line in result.stdout.splitlines():
                    if f":{port} " in line and "LISTENING" in line:
                        parts = line.split()
                        pid = parts[-1]
                        if pid.isdigit():
                            subprocess.run(["taskkill", "/F", "/PID", pid],
                                           capture_output=True, timeout=5)
            else:
                subprocess.run(["fuser", "-k", f"{port}/tcp"],
                               capture_output=True, timeout=5)
        except Exception:
            pass  # Best-effort — don't fail startup if we can't kill

    def _start_sse_mode(
        self,
        *,
        extra_server_args: list[str] | None = None,
        cdp_endpoint: str | None,
        port: int,
        startup_timeout_seconds: int,
        timeout_seconds: int,
        log: Any,
    ) -> PlaywrightMcpSseClient:
        """Start playwright-mcp in HTTP/SSE mode on Windows.

        Protocol:
          1. Kill any existing process on the port (stale from a previous run)
          2. Start server with --port=N (no PIPE — avoids Windows buffering issue)
          3. GET /sse → receive event:endpoint with sessionId
          4. POST /sse?sessionId=<id> for all subsequent JSON-RPC calls
          5. Responses arrive on the SSE stream
        """
        # Kill stale server from previous run (e.g. started with wrong --cdp-endpoint)
        log.info("Checking for stale playwright-mcp server on port %d...", port)
        self._kill_port(port)
        time.sleep(0.5)  # Give OS time to release the port

        server_args = [f"--port={port}"]
        if cdp_endpoint:
            server_args.append(f"--cdp-endpoint={cdp_endpoint}")
        # No --headless flag: browser window is visible so user can log in to LinkedIn
        # Append any extra args (e.g. --user-data-dir for session sharing)
        if extra_server_args:
            # Filter out the npx package name if accidentally included
            filtered = [a for a in extra_server_args if not a.startswith("@")]
            server_args.extend(filtered)

        npx = shutil.which("npx.cmd") or shutil.which("npx")
        if not npx:
            raise RuntimeError("npx not found on PATH; cannot start playwright-mcp")

        full_cmd = [npx, "@playwright/mcp"] + server_args
        log.info("Starting Playwright MCP (SSE on port %d): %s", port, " ".join(full_cmd))

        # Launch WITHOUT PIPE to avoid Windows buffering issue
        self._process = subprocess.Popen(
            full_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        client = PlaywrightMcpSseClient(port, timeout_seconds=timeout_seconds)
        deadline = time.time() + startup_timeout_seconds

        # Wait for SSE server to come up, then establish session
        client.connect(deadline)

        # Send initialize
        client.request("initialize", {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "coapplyer-ai", "version": "0.1.0"},
            "capabilities": {},
        })
        client.notify("initialized", {})
        log.info("Playwright MCP SSE server ready on port %d (session: %s)", port, client._session_path)
        return client

    @staticmethod
    def _find_mcp_cli_js() -> str | None:
        npm_exe = shutil.which("npm.cmd") or shutil.which("npm")
        if not npm_exe:
            return None
        try:
            prefix = subprocess.check_output(
                [npm_exe, "root", "-g"], text=True, timeout=10
            ).strip()
            cli = os.path.join(prefix, "@playwright", "mcp", "cli.js")
            if os.path.isfile(cli):
                return cli
        except Exception:
            pass
        return None

    @staticmethod
    def _resolve_stdio_launch(
        command: str | None,
        args: Iterable[str] | None,
        cdp_endpoint: str | None,
        log: Any,
    ) -> tuple[str, list[str]]:
        if args is not None:
            server_args = list(args)
        elif cdp_endpoint:
            server_args = [f"--cdp-endpoint={cdp_endpoint}"]
        else:
            server_args = ["--headless"]

        if command is not None:
            return command, server_args

        for candidate_cmd, candidate_prefix in PlaywrightMcpStdioSession._NPX_STRATEGIES:
            if shutil.which(candidate_cmd):
                full_args = candidate_prefix + server_args
                log.debug("Playwright MCP stdio launch: %s %s", candidate_cmd, " ".join(full_args))
                return candidate_cmd, full_args

        fallback_cmd, fallback_prefix = PlaywrightMcpStdioSession._NPX_STRATEGIES[-1]
        full_args = fallback_prefix + server_args
        log.warning("npx not found; using '%s %s'", fallback_cmd, " ".join(full_args))
        return fallback_cmd, full_args

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass
        if self._process is not None:
            pid = self._process.pid
            try:
                if self._process.poll() is None:
                    if sys.platform == "win32":
                        # Kill the entire process tree (npx + Chrome children)
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(pid)],
                            capture_output=True, timeout=5,
                        )
                    else:
                        self._process.terminate()
                    try:
                        self._process.wait(timeout=3)
                    except Exception:
                        pass
            except Exception:
                pass
            self._process = None

    def _load_tool_schemas(self) -> dict[str, ToolSchema]:
        result = self.client.request("tools/list", {}) or {}
        tools = result.get("tools", []) if isinstance(result, dict) else []
        schemas: dict[str, ToolSchema] = {}
        for tool in tools:
            if isinstance(tool, dict) and tool.get("name"):
                schemas[tool["name"]] = ToolSchema(
                    name=tool["name"],
                    input_schema=tool.get("inputSchema", {}) or {},
                )
        return schemas

    def tool_names(self) -> list[str]:
        return list(self.tool_schemas)

    def to_selector(self, by: str, value: str) -> str:
        by = (by or "").upper()
        if by == "XPATH":
            return f"xpath={value}"
        if by == "CSS_SELECTOR":
            return value
        if by == "ID":
            return f"#{value}"
        if by == "CLASS_NAME":
            return "." + ".".join(part for part in value.split() if part)
        if by == "TAG_NAME":
            return value
        if by == "LINK_TEXT":
            return f'text="{value}"'
        if by == "PARTIAL_LINK_TEXT":
            return f'text={value}'
        return value

    def has_tool(self, *candidate_names: str) -> bool:
        return any(name in self.tool_schemas for name in candidate_names)

    def _pick_tool_name(self, *candidate_names: str) -> str:
        for name in candidate_names:
            if name in self.tool_schemas:
                return name
        for candidate in candidate_names:
            candidate_lower = candidate.lower()
            for tool_name in self.tool_schemas:
                if candidate_lower in tool_name.lower():
                    return tool_name
        raise NotImplementedError(f"Playwright MCP server does not expose any of: {', '.join(candidate_names)}")

    def _schema_argument_name(self, tool_name: str, preferred: str, fallbacks: list[str]) -> str:
        schema = self.tool_schemas.get(tool_name)
        properties = list((schema.input_schema.get("properties") or {}).keys()) if schema else []
        if preferred in properties:
            return preferred
        for fallback in fallbacks:
            if fallback in properties:
                return fallback
        if properties:
            return properties[0]
        return preferred

    def call_tool(self, *candidate_names: str, **arguments: Any) -> Any:
        tool_name = self._pick_tool_name(*candidate_names)
        schema = self.tool_schemas.get(tool_name)
        payload: dict[str, Any] = {}

        if schema is not None:
            properties = list((schema.input_schema.get("properties") or {}).keys())
            for key, value in arguments.items():
                if key in properties:
                    payload[key] = value
                    continue
                aliases = {
                    "function": ["function", "fn", "code", "expression", "script"],
                    "code": ["code", "expression", "script"],
                    "url": ["url", "uri", "href"],
                    "selector": ["selector", "element", "locator"],
                    "text": ["text", "value", "label"],
                    "timeout_seconds": ["timeout", "timeout_seconds", "timeoutMs"],
                    "args": ["args", "arguments"],
                }.get(key, [key])
                matched = next((alias for alias in aliases if alias in properties), None)
                if matched is not None:
                    payload[matched] = value
                elif len(properties) == 1:
                    payload[properties[0]] = value
                else:
                    payload[key] = value
        else:
            payload = dict(arguments)

        result = self.client.request("tools/call", {"name": tool_name, "arguments": payload}) or {}
        content = result.get("content") if isinstance(result, dict) else None
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                if "text" in first:
                    return first["text"]
                if "data" in first:
                    return first["data"]
        return result

    def evaluate(self, code: str) -> Any:
        # browser_evaluate requires a JS function string, not raw code.
        # Wrap code in an IIFE-style arrow function.
        wrapped = f"() => {{ {code} }}"
        return self.call_tool("browser_evaluate", "evaluate", function=wrapped)
