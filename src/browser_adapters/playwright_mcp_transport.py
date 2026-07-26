from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ToolSchema:
    name: str
    input_schema: dict[str, Any]


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

    def start(self) -> None:
        if self._process is not None:
            return

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

            started = False
            startup_deadline = time.time() + self.startup_timeout_seconds
            last_error: Exception | None = None

            while time.time() < startup_deadline:
                per_attempt_timeout = min(12, max(2, startup_deadline - time.time()))
                try:
                    self._request_internal(
                        "initialize",
                        init_params,
                        timeout_seconds=per_attempt_timeout,
                        close_on_timeout=False,
                    )
                    started = True
                    break
                except TimeoutError as exc:
                    last_error = exc
                    if self._process is not None and self._process.poll() is not None:
                        break

            if not started:
                stderr_tail = " | ".join(self._stderr_lines[-10:])
                exit_code = self._process.poll() if self._process is not None else None
                self.close()
                detail = stderr_tail or (str(last_error) if last_error else "no diagnostics")
                raise TimeoutError(
                    f"Timed out waiting for MCP initialize after retries (exit={exit_code}). details: {detail}"
                )

            self.notify("initialized", {})
        except FileNotFoundError as exc:
            raise RuntimeError(f"Unable to start MCP command '{self.command}' (resolved to '{self._resolved_command}')") from exc

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
                # npm/npx may emit plain text before the MCP server starts writing frames.
                self._stderr_lines.append(f"stdout: {decoded}")
                if len(self._stderr_lines) > 100:
                    self._stderr_lines = self._stderr_lines[-100:]
                continue

            try:
                content_length = int(decoded.split(":", 1)[1].strip())
            except ValueError:
                self._stderr_lines.append(f"stdout: invalid content-length header '{decoded}'")
                if len(self._stderr_lines) > 100:
                    self._stderr_lines = self._stderr_lines[-100:]
                continue

            # Consume remaining header lines until the blank separator line.
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


class PlaywrightMcpStdioSession:
    def __init__(
        self,
        command: str = "npx",
        args: Iterable[str] | None = None,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ):
        self.client = JsonRpcStdioClient(
            command,
            args or ["--yes", "@playwright/mcp@latest"],
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
            startup_timeout_seconds=10,
        )
        self.client.start()
        self.tool_schemas: dict[str, ToolSchema] = self._load_tool_schemas()

    def close(self) -> None:
        self.client.close()

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

        lower_names = [name.lower() for name in self.tool_schemas]
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
        return self.call_tool("browser_evaluate", "evaluate", code=code)
