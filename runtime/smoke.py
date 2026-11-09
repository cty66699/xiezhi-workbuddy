#!/usr/bin/env python3
"""协智引擎真实冒烟运行辅助器。

只调用本地 xiezhi.py 账本命令；模型回答由 WorkBuddy 原生子代理写入 artifacts。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT = Path(__file__).resolve().parent.parent
CLI = PROJECT / "runtime" / "xiezhi.py"
ROOT = PROJECT / "runs"
CONFIG = PROJECT / "config"

EARLY_STAGES = [
    ("coordinate", "sol_controller", "coordinate.md"),
    ("analyze", "requirements_analyst", "analyze.md"),
    ("route", "luna_router", "route.md"),
    ("execute", "fast_executor", "execute-fast.md"),
    ("execute", "office_data_executor", "execute-office.md"),
    ("collect", "luna_router", "collect.md"),
    ("safety", "sol_controller", "safety.md"),
]
FINAL_STAGES = ["review", "adjudicate"]


def cli(*args: str) -> Dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(CLI), *args], cwd=str(PROJECT),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    payload = None
    depth, start, in_string, escaped = 0, None, False, False
    for index, char in enumerate(proc.stdout or ""):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    candidate = json.loads(proc.stdout[start:index + 1])
                    if isinstance(candidate, dict):
                        payload = candidate
                except json.JSONDecodeError:
                    pass
                start = None
    if proc.returncode != 0:
        raise RuntimeError(f"CLI failed: {' '.join(args)}\n{combined}")
    if payload is None:
        raise RuntimeError(f"CLI returned no JSON: {' '.join(args)}\n{combined}")
    return payload


def common(*extra: str) -> List[str]:
    return ["--root", str(ROOT), "--config", str(CONFIG), *extra]


def cmd_prepare(args: argparse.Namespace) -> None:
    preflight = cli("preflight", *common())
    if not preflight.get("ok"):
        raise RuntimeError(f"preflight failed: {preflight}")
    started = cli("start", *common(
        "--goal", args.goal, "--mode", "standard", "--source", *args.sources))
    run_id = started["runId"]
    run_dir = ROOT / run_id
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    tickets = []
    for stage, role, filename in EARLY_STAGES + [("adjudicate", "sol_controller", "adjudicate.md")]:
        out = artifacts / filename
        made = cli("ticket", *common(
            "--run-id", run_id, "--stage", stage, "--role", role,
            "--criteria", "输出必须包含结论、依据、限制和未解决项", "--output", str(out)))
        tickets.append({
            "stage": stage, "role": role, "ticket": made.get("ticketPath") or made.get("path") or made.get("ticket"),
            "output": str(out),
        })
    plan = {"runId": run_id, "runDir": str(run_dir), "preflight": preflight,
            "started": started, "tickets": tickets, "stageOrder": [s for s, _, _ in EARLY_STAGES] + FINAL_STAGES}
    plan_path = run_dir / "smoke-plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "runId": run_id, "plan": str(plan_path),
                      "tickets": tickets}, ensure_ascii=False, indent=2))


def cmd_review_ticket(args: argparse.Namespace) -> None:
    recommendation = cli("reviewer", *common("--run-id", args.run_id))
    role = recommendation.get("recommendedRole") or "business_reviewer"
    run_dir = ROOT / args.run_id
    out = run_dir / "artifacts" / "review.md"
    made = cli("ticket", *common(
        "--run-id", args.run_id, "--stage", "review", "--role", role,
        "--criteria", "按原需求、验收标准和全部阶段产物逐项复核；不得预设通过",
        "--output", str(out)))
    data = {"runId": args.run_id, "recommendation": recommendation,
            "ticket": made.get("ticketPath") or made.get("path") or made.get("ticket"),
            "role": role, "output": str(out)}
    path = run_dir / "review-ticket.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "reviewTicket": str(path), **data}, ensure_ascii=False, indent=2))


def cmd_record(args: argparse.Namespace) -> None:
    evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    entries = evidence.get("entries", [])
    run_id = evidence["runId"]
    by_stage: Dict[str, List[Dict[str, Any]]] = {}
    for entry in entries:
        by_stage.setdefault(entry["stage"], []).append(entry)
    stage_order = evidence.get("stageOrder") or list(by_stage.keys())
    for stage in stage_order:
        group = by_stage.get(stage, [])
        if not group:
            continue
        for entry in group:
            ticket_path = Path(entry["ticket"])
            ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
            model_id = ticket["requestedModelId"]
            output = Path(entry.get("output") or ticket.get("outputPath") or "")
            if not output.is_file():
                raise RuntimeError(f"缺少子代理产物: {output}")
            cli("receipt", *common(
                "--run-id", run_id, "--ticket", str(ticket_path),
                "--status", entry.get("status", "passed"),
                "--agent-id", entry.get("agentId", ""),
                "--task-id", entry.get("taskId", ""),
                "--actual-model", model_id,
                "--model-evidence", entry.get("modelEvidence", "WorkBuddy 原生子代理显式 model 参数"),
                "--raw-output", str(output), "--artifact", str(output)))
        cli("advance", *common("--run-id", run_id, "--stage", stage))
    result: Dict[str, Any] = {"ok": True, "runId": run_id, "recordedStages": stage_order}
    if args.final:
        result["verify"] = cli("verify", *common("--run-id", run_id))
        result["report"] = cli("report", *common("--run-id", run_id))
        result["status"] = cli("status", *common("--run-id", run_id))
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--goal", required=True)
    p.add_argument("--sources", nargs="+", required=True)
    p.set_defaults(handler=cmd_prepare)
    p = sub.add_parser("review-ticket")
    p.add_argument("--run-id", required=True)
    p.set_defaults(handler=cmd_review_ticket)
    p = sub.add_parser("record")
    p.add_argument("--evidence", required=True)
    p.add_argument("--final", action="store_true")
    p.set_defaults(handler=cmd_record)
    args = parser.parse_args()
    try:
        args.handler(args)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
