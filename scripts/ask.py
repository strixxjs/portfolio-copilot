"""CLI SSE client: python scripts/ask.py --user alice@example.test "What response was given to RFI P1-RFI-003?"

Renders the route, each tool call and result, streams tokens of the current turn (discarding interim
text of a turn that ends in a tool call), then prints the validated answer, citations, status and usage.
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--user", default="carol@example.test")
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--project")
    a = ap.parse_args()
    body = {"question": a.question, **({"project_id": a.project} if a.project else {})}
    turn_text: dict[int, str] = {}
    with httpx.stream("POST", f"{a.url}/ask", json=body, headers={"X-User-Email": a.user}, timeout=120) as r:
        if r.status_code != 200:
            print(f"HTTP {r.status_code}: {r.read().decode()}")
            return 1
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:])
            t = ev["type"]
            if t == "route":
                print(f"[route] {ev['route']} ({ev['reason']}) tools={ev['tools_enabled']}")
            elif t == "token":
                turn_text[ev["turn"]] = turn_text.get(ev["turn"], "") + ev["text"]
                sys.stdout.write(ev["text"])
                sys.stdout.flush()
            elif t == "tool_call":
                if turn_text.get(ev["turn"]):
                    print("\n  (interim text of this turn discarded)")
                    turn_text[ev["turn"]] = ""
                rep = " [repair]" if ev["repair"] else ""
                print(f"[tool_call]{rep} {ev['tool']} {json.dumps(ev['args'])}")
            elif t == "tool_result_summary":
                status = "ok" if ev["ok"] else f"ERROR {ev['error']['code']}: {ev['error']['message']}"
                print(f"[result] {ev['tool']} {status} — {ev['summary']}")
                if ev.get("sql"):
                    print(f"         sql: {ev['sql']}")
            elif t == "citations":
                print("\n\n=== answer (validated) ===\n" + ev["answer"])
                print(
                    "citations: "
                    + ", ".join(c["source_id"] for c in ev["citations"] if c["valid"])
                    + (f"  (removed {ev['removed']})" if ev["removed"] else "")
                )
            elif t == "error":
                print(f"\n[error] {ev['code']}: {ev['message']}")
            elif t == "done":
                u = ev["usage"]
                print(
                    f"[done] status={ev['status']} steps={ev['steps']} {ev['duration_ms']} ms · "
                    f"{u['llm_calls']} LLM calls ({u['cache_hits']} cached) · {u['total_tokens']} tokens · "
                    f"${u['cost_usd']:.6f} list price" + (" (estimated)" if u["estimated"] else "")
                )
                flags = {k: v for k, v in ev["flags"].items() if v}
                if flags:
                    print(f"       flags: {flags}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
