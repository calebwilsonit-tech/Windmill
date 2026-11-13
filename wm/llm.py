"""Talks to llama-server's OpenAI-compatible API: streaming text, thinking, and tool calls."""
import json
import re
import urllib.error
import urllib.request

from . import config

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


class LLMError(Exception):
    pass


def est_tokens(obj):
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return int(len(s) / 3.6)


class LLM:
    def __init__(self, cfg=None):
        self.cfg = cfg or config.load()
        self.base = config.api_base(self.cfg)
        self.usage = {"prompt": 0, "completion": 0}

    def chat(self, messages, tools=None, temperature=0.6, max_tokens=8192, on_text=None, on_think=None,
             stream=True, extra=None):
        """Returns {"content", "reasoning", "tool_calls": [{"id","name","arguments"}], "finish"}."""
        body = {"model": "local", "messages": messages, "temperature": temperature, "top_p": 0.95,
                "top_k": 20, "max_tokens": max_tokens, "stream": stream}
        if tools:
            body["tools"] = tools
        if extra:
            body.update(extra)
        req = urllib.request.Request(f"{self.base}/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        content, reasoning, calls, finish = [], [], {}, None
        try:
            resp = urllib.request.urlopen(req, timeout=7200)
        except urllib.error.HTTPError as e:
            raise LLMError(f"model server error {e.code}: {e.read().decode(errors='replace')[:400]}") from e
        except urllib.error.URLError as e:
            raise LLMError(f"can't reach the model server ({e.reason})") from e
        try:
            if not stream:
                data = json.loads(resp.read())
                msg = data["choices"][0]["message"]
                content.append(msg.get("content") or "")
                reasoning.append(msg.get("reasoning_content") or "")
                for i, tc in enumerate(msg.get("tool_calls") or []):
                    calls[i] = {"id": tc.get("id"), "name": tc["function"]["name"], "args": tc["function"].get("arguments", "")}
                finish = data["choices"][0].get("finish_reason")
                self._usage(data)
            else:
                for raw in resp:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                    except ValueError:
                        continue
                    self._usage(data)
                    if not data.get("choices"):
                        continue
                    ch = data["choices"][0]
                    d = ch.get("delta", {})
                    if d.get("reasoning_content"):
                        reasoning.append(d["reasoning_content"])
                        if on_think:
                            on_think(d["reasoning_content"])
                    if d.get("content"):
                        content.append(d["content"])
                        if on_text:
                            on_text(d["content"])
                    for tc in d.get("tool_calls") or []:
                        i = tc.get("index", 0)
                        c = calls.setdefault(i, {"id": None, "name": "", "args": ""})
                        c["id"] = tc.get("id") or c["id"]
                        fn = tc.get("function", {})
                        c["name"] += fn.get("name") or ""
                        c["args"] += fn.get("arguments") or ""
                    finish = ch.get("finish_reason") or finish
        finally:
            resp.close()
        text = "".join(content)
        # Some templates leave thinking or tool calls inline in the text
        if "</think>" in text:
            th, text = text.split("</think>", 1)
            reasoning.append(th.replace("<think>", ""))
        tool_calls = []
        for i in sorted(calls):
            c = calls[i]
            tool_calls.append({"id": c["id"] or f"call_{i}", "name": c["name"], "arguments": parse_args(c["args"])})
        if not tool_calls and "<tool_call>" in text:
            for j, m in enumerate(TOOL_CALL_RE.finditer(text)):
                try:
                    obj = json.loads(m.group(1))
                    tool_calls.append({"id": f"call_t{j}", "name": obj.get("name", ""),
                                       "arguments": obj.get("arguments") or obj.get("parameters") or {}})
                except ValueError:
                    pass
            text = TOOL_CALL_RE.sub("", text).strip()
        return {"content": text.strip(), "reasoning": "".join(reasoning).strip(), "tool_calls": tool_calls,
                "finish": finish}

    def _usage(self, data):
        u = data.get("usage") or {}
        if u:
            self.usage["prompt"] = u.get("prompt_tokens", self.usage["prompt"])
            self.usage["completion"] += u.get("completion_tokens", 0)

    def simple(self, system, user, temperature=0.4, max_tokens=8192):
        """One fresh-context call; returns the final answer text."""
        r = self.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                      temperature=temperature, max_tokens=max_tokens, stream=True)
        return r["content"]


def parse_args(s):
    if isinstance(s, dict):
        return s
    s = (s or "").strip()
    if not s:
        return {}
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {"value": v}
    except ValueError:
        # common local-model glitch: trailing junk after the JSON object
        depth = 0
        for i, ch in enumerate(s):
            depth += ch == "{"
            depth -= ch == "}"
            if depth == 0 and i:
                try:
                    return json.loads(s[:i + 1])
                except ValueError:
                    break
        return {"_raw": s, "_error": "arguments were not valid JSON"}
