"""Tests for CLI agent integration (Claude Code & Gemini CLI), Windmill MCP server,
local AI sub-agents, and continuous supervision."""
import io
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from wm import cli_agents, config, gui, mcp_server, subagents


class FakeLocalAgent:
    """Simulates a local Windmill Agent editing files in a project folder."""

    def __init__(self, subagent, cfg, file_to_write="hello.txt"):
        self.subagent = subagent
        self.file_to_write = file_to_write
        self.turns = []

    def send(self, prompt):
        self.turns.append(prompt)
        target = self.subagent.root / self.file_to_write
        if len(self.turns) == 1:
            target.write_text('STATUS = "v1 draft"\n', encoding="utf-8")
            return f"Created {self.file_to_write} (v1)."
        target.write_text('STATUS = "v2 fixed"\n', encoding="utf-8")
        return f"Fixed {self.file_to_write} per supervisor feedback (v2)."


def _init_git_repo(path: Path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@local"], cwd=path, check=True)
    (path / "README.md").write_text("# Demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def test_plugin_install_and_uninstall_claude_and_gemini(tmp_path):
    # Pre-populate existing configs to ensure Windmill merges without clobbering
    (tmp_path / ".claude.json").write_text(json.dumps({"otherKey": 123, "mcpServers": {"keepme": {"command": "echo"}}}), encoding="utf-8")
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".gemini" / "settings.json").write_text(json.dumps({"theme": "dark"}), encoding="utf-8")

    assert not cli_agents.plugin_status("claude", home=tmp_path)["plugin_installed"]
    assert not cli_agents.plugin_status("gemini", home=tmp_path)["plugin_installed"]

    ok_c, _ = cli_agents.install_plugin("claude", home=tmp_path)
    ok_g, _ = cli_agents.install_plugin("gemini", home=tmp_path)
    assert ok_c and ok_g

    assert cli_agents.plugin_status("claude", home=tmp_path)["plugin_installed"]
    assert cli_agents.plugin_status("gemini", home=tmp_path)["plugin_installed"]

    # Verify Claude files and preserved keys
    c_cfg = json.loads((tmp_path / ".claude.json").read_text(encoding="utf-8"))
    assert c_cfg["otherKey"] == 123
    assert "keepme" in c_cfg["mcpServers"]
    assert "windmill" in c_cfg["mcpServers"]
    assert "mcp-serve" in c_cfg["mcpServers"]["windmill"]["args"]
    assert "$ARGUMENTS" in (tmp_path / ".claude" / "commands" / "windmill.md").read_text(encoding="utf-8")
    assert "$ARGUMENTS" in (tmp_path / ".claude" / "commands" / "windmill-swarm.md").read_text(encoding="utf-8")

    # Verify Gemini files and preserved keys
    g_cfg = json.loads((tmp_path / ".gemini" / "settings.json").read_text(encoding="utf-8"))
    assert g_cfg["theme"] == "dark"
    assert "windmill" in g_cfg["mcpServers"]
    assert "{{args}}" in (tmp_path / ".gemini" / "commands" / "windmill.toml").read_text(encoding="utf-8")
    assert "{{args}}" in (tmp_path / ".gemini" / "commands" / "windmill-swarm.toml").read_text(encoding="utf-8")

    # Uninstall both and verify clean removal while keeping user settings
    cli_agents.uninstall_plugin("claude", home=tmp_path)
    cli_agents.uninstall_plugin("gemini", home=tmp_path)
    assert not cli_agents.plugin_status("claude", home=tmp_path)["plugin_installed"]
    assert not cli_agents.plugin_status("gemini", home=tmp_path)["plugin_installed"]
    c_after = json.loads((tmp_path / ".claude.json").read_text(encoding="utf-8"))
    assert c_after["otherKey"] == 123 and "keepme" in c_after["mcpServers"] and "windmill" not in c_after["mcpServers"]


def test_expand_slash_commands():
    p1, exp1 = cli_agents.expand_slash_command("/windmill build a calculator", local_model="qwen3")
    assert exp1 and "build a calculator" in p1 and "qwen3" in p1 and "spawn_local_subagent" in p1

    p2, exp2 = cli_agents.expand_slash_command("/swarm build ui and tests")
    assert exp2 and "build ui and tests" in p2 and "send_subagent_feedback" in p2

    p3, exp3 = cli_agents.expand_slash_command("explain this function")
    assert not exp3 and p3 == "explain this function"


def test_mcp_server_protocol_and_subagent_lifecycle(tmp_path):
    _init_git_repo(tmp_path)
    mgr = subagents.SubAgentManager(
        root=tmp_path,
        agent_factory=lambda sa, cfg: FakeLocalAgent(sa, cfg, "feature.py"),
        persist=False,
    )
    srv = mcp_server.WindmillMCPServer(project=tmp_path, manager=mgr)

    # 1. Initialize
    r_init = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert r_init["result"]["serverInfo"]["name"] == "windmill"
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    # 2. List tools
    r_tools = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in r_tools["result"]["tools"]}
    assert {"list_local_models", "spawn_local_subagent", "send_subagent_feedback", "approve_subagent",
            "get_subagent_status", "list_local_subagents", "check_project"} <= names

    # 3. Spawn local sub-agent
    r_spawn = srv.handle({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "spawn_local_subagent", "arguments": {"name": "Feature Bot", "task": "Create feature.py"}},
    })
    rep = json.loads(r_spawn["result"]["content"][0]["text"])
    assert rep["agent_id"] == "bot-1"
    assert rep["status"] == "awaiting_review"
    assert "feature.py" in rep["files_changed"]
    assert "v1 draft" in rep["diff"]

    # 4. Send supervisor feedback -> same bot fixes it
    r_fix = srv.handle({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "send_subagent_feedback", "arguments": {"agent_id": "bot-1", "feedback": "Change draft to fixed"}},
    })
    rep2 = json.loads(r_fix["result"]["content"][0]["text"])
    assert rep2["round"] == 2
    assert "v2 fixed" in rep2["diff"]

    # 5. Approve sub-agent
    r_app = srv.handle({
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "approve_subagent", "arguments": {"agent_id": "bot-1", "note": "Looks great"}},
    })
    rep3 = json.loads(r_app["result"]["content"][0]["text"])
    assert rep3["status"] == "passed"


def test_mcp_serve_subprocess_clean_stdout(tmp_path):
    """Ensure `python3 windmill.py mcp-serve` produces strictly valid JSON-RPC on stdout."""
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    payload = "\n".join(json.dumps(r) for r in reqs) + "\n"
    r = subprocess.run(
        [sys.executable, "-u", str(config.KIT / "windmill.py"), "mcp-serve", "--project", str(tmp_path)],
        input=payload, capture_output=True, text=True, timeout=20,
    )
    assert r.returncode == 0, r.stderr
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["result"]["serverInfo"]["name"] == "windmill"
    assert len(json.loads(lines[1])["result"]["tools"]) >= 7


def test_continuous_supervision_loop(tmp_path):
    _init_git_repo(tmp_path)
    events = []
    calls = {"n": 0}

    def fake_cli(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": True, "text": json.dumps([
                {"name": "Core Bot", "task": "Build core module", "files": ["core.py"]},
                {"name": "Test Bot", "task": "Add tests", "files": ["test_core.py"]},
            ])}
        if calls["n"] == 2:
            # First review of Core Bot -> request a fix
            return {"ok": True, "text": "## Review\nStill a draft.\n## Fixes needed\nReplace v1 draft with v2 fixed.\nVERDICT: FAIL"}
        # Round 2 of Core Bot and Round 1 of Test Bot -> pass
        return {"ok": True, "text": "## Review\nAll requirements met.\n## Fixes needed\nNone\nVERDICT: PASS"}

    idx = {"i": 0}
    def factory(sa, cfg):
        idx["i"] += 1
        return FakeLocalAgent(sa, cfg, f"chunk_{idx['i']}.py")

    res = subagents.supervise_goal(
        "Build core module and tests",
        tmp_path,
        provider="claude",
        max_rounds=3,
        on_event=events.append,
        cli_ask_fn=fake_cli,
        agent_factory=factory,
    )
    assert res["ok"] is True
    assert len(res["agents"]) == 2
    assert res["agents"][0]["round"] == 2  # fixed once after FAIL review
    assert res["agents"][0]["status"] == "passed"
    assert res["agents"][1]["round"] == 1
    assert res["agents"][1]["status"] == "passed"
    etypes = [e["type"] for e in events]
    assert "subagent_start" in etypes
    assert "subagent_fix" in etypes
    assert "subagent_review" in etypes


def test_run_stream_parses_claude_and_gemini_events(tmp_path, monkeypatch):
    # Create a tiny mock CLI executable that emits stream-json events
    mock_claude = tmp_path / "mock_claude.py"
    mock_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "_ = sys.stdin.read()\n"
        "msgs = [\n"
        "  {'type': 'assistant', 'session_id': 'sess-42', 'message': {'content': [\n"
        "    {'type': 'tool_use', 'id': 't1', 'name': 'mcp__windmill__spawn_local_subagent', 'input': {'name': 'UI Bot', 'task': 'Build UI'}}\n"
        "  ]}},\n"
        "  {'type': 'user', 'message': {'content': [\n"
        "    {'type': 'tool_result', 'tool_use_id': 't1', 'content': json.dumps({'agent_id': 'bot-1', 'name': 'UI Bot', 'status': 'awaiting_review', 'checks_ok': True, 'files_changed': ['ui.js'], 'summary': 'Built UI'})}\n"
        "  ]}},\n"
        "  {'type': 'assistant', 'session_id': 'sess-42', 'message': {'content': [\n"
        "    {'type': 'tool_use', 'id': 't2', 'name': 'mcp__windmill__approve_subagent', 'input': {'agent_id': 'bot-1', 'note': 'Verified!'}},\n"
        "    {'type': 'text', 'text': 'All done.'}\n"
        "  ]}},\n"
        "  {'type': 'result', 'session_id': 'sess-42', 'result': 'All done.'}\n"
        "]\n"
        "for m in msgs:\n"
        "  print(json.dumps(m), flush=True)\n",
        encoding="utf-8",
    )
    mock_claude.chmod(mock_claude.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(cli_agents, "cli_path", lambda prov: str(mock_claude))

    events = []
    res = cli_agents.run_stream("claude", "/windmill build a button", tmp_path, on_event=events.append)
    assert res["ok"] is True
    assert res["session_id"] == "sess-42"
    assert "All done." in res["text"]
    etypes = [e["type"] for e in events]
    assert "subagent_start" in etypes
    assert "subagent_done" in etypes
    assert "subagent_review" in etypes


def test_chatrun_detect_cli_request(tmp_path):
    run = gui.ChatRun("abc123", str(tmp_path))
    cfg = {"cli_provider": "gemini"}
    assert run._detect_cli_request("/windmill make a game", "code", cfg) == ("gemini", "/windmill make a game", False)
    assert run._detect_cli_request("/claude make a game", "chat", cfg) == ("claude", "/windmill make a game", False)
    assert run._detect_cli_request("/gemini make a game", "chat", cfg) == ("gemini", "/windmill make a game", False)
    assert run._detect_cli_request("/supervise claude fix bugs", "chat", cfg) == ("claude", "fix bugs", True)
    assert run._detect_cli_request("hello", "claude", cfg) == ("claude", "hello", False)
    assert run._detect_cli_request("hello", "code", cfg) == (None, "hello", False)


def test_per_chat_project_sessions(tmp_path, monkeypatch):
    from wm import sessions
    monkeypatch.setattr(sessions, "DB", tmp_path / "sessions.db")
    s1 = sessions.Session.new()
    s2 = sessions.Session.new(str(tmp_path))
    assert s1.project == ""
    assert s2.project == str(tmp_path)

    s1.set_project(str(tmp_path))
    assert sessions.Session.load(s1.id).project == str(tmp_path)

    s1.set_project("")
    assert sessions.Session.load(s1.id).project == ""
    assert sessions.Session.load(s2.id).project == str(tmp_path)

