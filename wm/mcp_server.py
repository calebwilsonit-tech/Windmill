"""Windmill MCP server (stdio JSON-RPC 2.0): lets Claude Code and Gemini CLI spawn and supervise
local AI sub-agents powered by Windmill's installed local models.

Run with:  windmill mcp-serve [--project <folder>]

Tools exposed to Claude Code / Gemini CLI:
  - list_local_models       list local GGUF models installed on this PC
  - spawn_local_subagent    spawn a local AI worker bot to build a focused chunk & return its diff/checks
  - send_subagent_feedback  send review feedback to an existing local bot so it fixes its work & re-checks
  - approve_subagent        mark a local sub-agent's work as reviewed & approved
  - get_subagent_status     inspect a sub-agent's current status, diff, tool calls & review history
  - list_local_subagents    list all local sub-agents created in this session
  - check_project           run Windmill's project checks (build, tests, sloppy-code guard)
"""
import contextlib
import json
import os
import sys
from pathlib import Path

from . import checks, config, subagents

PROTOCOL = "2025-06-18"

TOOLS = [
    {
        "name": "list_local_models",
        "description": "List all local AI models (GGUF) installed on this computer that Windmill can run for local sub-agents.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "spawn_local_subagent",
        "description": (
            "Spawn a LOCAL AI sub-agent powered by a local model installed on this PC (zero cloud sub-agent cost). "
            "The local sub-agent reads files, writes/edits code, runs project checks, and returns its summary, "
            "changed files, git diff, and check results so you can review its work right away."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short role name for this local bot (e.g. 'UI Builder', 'API Worker', 'Test Writer')."},
                "task": {"type": "string", "description": "Clear, self-contained instructions for what this local sub-agent should build or edit."},
                "model": {"type": "string", "description": "Optional local model name or path (from list_local_models). Omit to use Windmill's default coding model."},
                "files": {"type": "array", "items": {"type": "string"}, "description": "Optional list of files this sub-agent should focus on."},
                "project_dir": {"type": "string", "description": "Optional project folder path (defaults to current working directory)."},
                "max_steps": {"type": "integer", "description": "Optional max tool calls for this sub-agent (default 35)."},
            },
            "required": ["name", "task"],
        },
    },
    {
        "name": "send_subagent_feedback",
        "description": (
            "Send your review feedback / exact fix instructions to an existing local sub-agent. "
            "The same local bot resumes with its full memory, applies the fixes, re-runs project checks, "
            "and returns the updated diff and check results for your next review."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "The agent_id returned by spawn_local_subagent (e.g. 'bot-1')."},
                "feedback": {"type": "string", "description": "Concrete issues found and exact fixes the local sub-agent should make."},
            },
            "required": ["agent_id", "feedback"],
        },
    },
    {
        "name": "approve_subagent",
        "description": "Mark a local sub-agent's chunk as reviewed and approved after verifying its work.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "The agent_id to approve (e.g. 'bot-1')."},
                "note": {"type": "string", "description": "Optional brief approval note."},
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "get_subagent_status",
        "description": "Get the status, summary, files changed, git diff, and review history of a local sub-agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "The agent_id to inspect."},
                "include_diff": {"type": "boolean", "description": "Include git diff (default true)."},
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "list_local_subagents",
        "description": "List all local AI sub-agents created in this session and their current status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "check_project",
        "description": "Run Windmill's project checks (build, tests, lint, sloppy-code guard) in the project folder.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_dir": {"type": "string", "description": "Optional project folder path."},
            },
        },
    },
]


def _version():
    try:
        return (config.KIT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


class WindmillMCPServer:
    """Pure-Python JSON-RPC 2.0 handler for Windmill's MCP tools."""

    def __init__(self, project=None, manager=None):
        self.root = Path(project or os.getcwd()).resolve()
        self.manager = manager or subagents.SubAgentManager(root=self.root)

    def _call_tool(self, name, args):
        args = args or {}
        if name == "list_local_models":
            return {"models": subagents.list_local_models()}
        if name == "spawn_local_subagent":
            return self.manager.spawn(
                name=args.get("name") or "Local Bot",
                task=args.get("task") or "",
                model=args.get("model"),
                project_dir=args.get("project_dir") or str(self.root),
                files_scope=args.get("files"),
                max_steps=args.get("max_steps"),
            )
        if name == "send_subagent_feedback":
            return self.manager.feedback(args.get("agent_id", ""), args.get("feedback", ""))
        if name == "approve_subagent":
            return self.manager.approve(args.get("agent_id", ""), args.get("note", ""))
        if name == "get_subagent_status":
            return self.manager.status(args.get("agent_id"), include_diff=bool(args.get("include_diff", True)))
        if name == "list_local_subagents":
            return self.manager.status()
        if name == "check_project":
            target = Path(args.get("project_dir") or self.root).resolve()
            ok, out = checks.run(target)
            return {"ok": bool(ok), "output": (out or "")[:6000]}
        raise ValueError(f"Unknown tool: {name}")

    def handle(self, msg):
        """Process one JSON-RPC request dict -> response dict or None (for notifications)."""
        method = msg.get("method")
        req_id = msg.get("id")
        params = msg.get("params") or {}

        if req_id is None:
            return None  # notification (e.g. notifications/initialized)

        try:
            if method == "initialize":
                proto = params.get("protocolVersion") or PROTOCOL
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": proto,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "windmill", "version": _version()},
                    },
                }
            if method == "ping":
                return {"jsonrpc": "2.0", "id": req_id, "result": {}}
            if method == "tools/list":
                return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
            if method == "tools/call":
                tname = params.get("name", "")
                targs = params.get("arguments") or {}
                # Redirect any stray stdout prints from Windmill internals to stderr so JSON-RPC stays valid
                with contextlib.redirect_stdout(sys.stderr):
                    res = self._call_tool(tname, targs)
                is_err = isinstance(res, dict) and bool(res.get("error")) and not res.get("agent_id")
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}],
                        "isError": is_err,
                    },
                }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        except Exception as e:  # noqa: BLE001
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": f"{type(e).__name__}: {e}"},
            }


def serve_stdio(project=None, stdin=None, stdout=None):
    """Run the MCP server loop over stdio until EOF."""
    inp = stdin or sys.stdin
    out = stdout or sys.stdout
    srv = WindmillMCPServer(project=project)
    for raw in inp:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        resp = srv.handle(msg)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out.flush()


def main(args=()):
    project = None
    i = 0
    while i < len(args):
        if args[i] == "--project" and i + 1 < len(args):
            project = args[i + 1]
            i += 2
        else:
            i += 1
    serve_stdio(project=project)
