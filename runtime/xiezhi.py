#!/usr/bin/env python3
"""协智引擎（WorkBuddy 原生版）本地运行时.

负责本地任务账本、票据、回执、阶段推进、返修、复核推荐、一致性校验、
单文件 HTML 报告与自检。仅使用 Python 3 标准库；不联网、不调用任何
模型 API、不生成模型回答（模型回答由宿主 WorkBuddy 会话/子代理产生，
本脚本只负责状态、哈希、证据与报告）。

用法::

    python xiezhi.py <command> [options]

命令: preflight / start / ticket / receipt / advance / reviewer /
      revise / verify / report / status / selftest
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DEFAULT_CONFIG_DIR = PROJECT_DIR / "config"
DEFAULT_ROOT = Path.cwd() / "runs"

ALL_STAGES: List[str] = [
    "coordinate", "analyze", "route", "execute",
    "collect", "safety", "review", "adjudicate",
]
# revise 时重置的阶段；coordinate/analyze/route 证据保留
REVISE_RESET_STAGES: List[str] = ["execute", "collect", "safety", "review", "adjudicate"]
# 复核独立性计算时视为“作者”的执行/汇总阶段
REVIEW_INPUT_STAGES: List[str] = ["execute", "collect"]

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_API_KEY_RE = re.compile(r"(?i)api[_-]?key|apikey|secret|password|credential|token[_-]?plan")
_URL_RE = re.compile(r"https?://", re.IGNORECASE)
_HASH_CHUNK = 1024 * 1024


class XiezhiError(Exception):
    """业务错误：消息打印给用户，可携带结构化附加数据。"""

    def __init__(self, message: str, data: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.data: Dict[str, Any] = data or {}


# ---------------------------------------------------------------------------
# 基础工具：时间、哈希、JSON 原子读写
# ---------------------------------------------------------------------------

def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256_file(path: Path) -> str:
    """分块读取文件并返回 SHA256 十六进制摘要。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_text(path: Path, text: str) -> None:
    """UTF-8 写盘并原子替换（临时文件 + os.replace）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# 配置加载与预检辅助
# ---------------------------------------------------------------------------

def load_configs(config_dir: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    catalog_path = config_dir / "model-catalog.json"
    map_path = config_dir / "model-map.json"
    if not catalog_path.is_file() or not map_path.is_file():
        raise XiezhiError(f"配置目录缺少文件: {config_dir}（需要 model-catalog.json 与 model-map.json）")
    try:
        catalog = read_json(catalog_path)
        model_map = read_json(map_path)
    except json.JSONDecodeError as exc:
        raise XiezhiError(f"配置 JSON 解析失败: {exc}") from exc
    if not isinstance(catalog.get("models"), dict) or not isinstance(model_map.get("roles"), dict):
        raise XiezhiError("配置结构不完整（缺少 catalog.models 或 model-map.roles）")
    return catalog, model_map


def _agent_definition_status(model_map: Dict[str, Any]) -> Tuple[List[Path], List[str], List[str]]:
    """在已安装路径中查找各角色的子代理定义文件，返回 (目录, 命中, 缺失)。"""
    raw_paths = model_map.get("dispatch", {}).get("agentDefinitionPaths", [])
    dirs: List[Path] = []
    for raw in raw_paths:
        expanded = str(raw).replace("<workspace>", str(PROJECT_DIR))
        candidate = Path(os.path.expanduser(expanded))
        if candidate.is_dir():
            dirs.append(candidate)
    found: List[str] = []
    missing: List[str] = []
    for role, rspec in sorted(model_map.get("roles", {}).items()):
        name = rspec.get("agentName", "")
        hit: Optional[str] = None
        for directory in dirs:
            for ext in (".md", ".json", ".yaml", ".yml"):
                cand = directory / f"{name}{ext}"
                if cand.is_file():
                    hit = str(cand)
                    break
            if hit:
                break
        if hit:
            found.append(f"{role}: {hit}")
        else:
            missing.append(f"{role}({name})")
    return dirs, found, missing


# ---------------------------------------------------------------------------
# 运行数据辅助
# ---------------------------------------------------------------------------

def resolve_run_dir(root: Path, run_id: str) -> Path:
    if not run_id or not _RUN_ID_RE.match(run_id) or run_id in (".", ".."):
        raise XiezhiError(f"非法 run-id: {run_id!r}")
    run_dir = root / run_id
    if not run_dir.is_dir():
        raise XiezhiError(f"运行不存在: {run_id}（root={root}）")
    return run_dir


def load_run_state(run_dir: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    spec_path = run_dir / "task-spec.json"
    ledger_path = run_dir / "run-ledger.json"
    for path in (spec_path, ledger_path):
        if not path.is_file():
            raise XiezhiError(f"运行数据缺失: {path}")
    return read_json(spec_path), read_json(ledger_path)


def save_ledger(run_dir: Path, ledger: Dict[str, Any]) -> None:
    ledger["updatedAt"] = _utc_iso()
    atomic_write_json(run_dir / "run-ledger.json", ledger)


def collect_tickets(run_dir: Path) -> List[Dict[str, Any]]:
    directory = run_dir / "tickets"
    if not directory.is_dir():
        return []
    return [read_json(p) for p in sorted(directory.glob("*.json"))]


def collect_receipts(run_dir: Path) -> List[Dict[str, Any]]:
    directory = run_dir / "receipts"
    if not directory.is_dir():
        return []
    items = [read_json(p) for p in sorted(directory.glob("*.json"))]
    items.sort(key=lambda r: str(r.get("receiptId", "")))
    return items


def mode_stage_list(model_map: Dict[str, Any], mode: str) -> List[str]:
    modes = model_map.get("modes", {})
    if mode not in modes:
        raise XiezhiError(f"未知模式: {mode}（可用: {', '.join(modes)}）")
    return list(modes[mode].get("stages", []))


def first_pending(ledger: Dict[str, Any], stage_list: Sequence[str]) -> Optional[str]:
    for stage in stage_list:
        if ledger.get("stages", {}).get(stage, {}).get("status") == "pending":
            return stage
    return None


def hash_source_files(paths: Sequence[str]) -> List[Dict[str, str]]:
    refs: List[Dict[str, str]] = []
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            raise XiezhiError(f"来源文件不存在: {raw}")
        refs.append({"path": str(path.resolve()), "sha256": sha256_file(path)})
    refs.sort(key=lambda r: r["path"])
    return refs


def compute_idempotency_key(goal: str, mode: str, sources: Sequence[Dict[str, str]]) -> str:
    digest = hashlib.sha256()
    digest.update(mode.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(goal.encode("utf-8"))
    for source in sources:
        digest.update(b"\x00")
        digest.update(source["path"].encode("utf-8"))
        digest.update(b"\x00")
        digest.update(source["sha256"].encode("ascii"))
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------

def cmd_preflight(args: argparse.Namespace) -> Dict[str, Any]:
    """预检：模型目录、角色绑定、禁止字段与子代理定义。"""
    catalog, model_map = load_configs(Path(args.config))
    errors: List[str] = []
    warnings: List[str] = []
    models: Dict[str, Any] = catalog.get("models", {})
    auto_tiers = catalog.get("excluded", {}).get(
        "autoTiers", ["Auto", "fast-model", "balanced-model", "deep-model"])

    def check_model_id(model_id: Optional[str], where: str) -> None:
        if not model_id:
            errors.append(f"{where}: 缺少 modelId")
            return
        if model_id.startswith("custom-local:"):
            errors.append(f"{where}: 禁止 custom-local 模型 {model_id}")
            return
        if model_id in auto_tiers:
            errors.append(f"{where}: 禁止自动档位模型 {model_id}")
            return
        entry = models.get(model_id)
        if entry is None:
            errors.append(f"{where}: 模型 {model_id} 不在 model-catalog 中")
        elif not entry.get("builtin"):
            errors.append(f"{where}: 模型 {model_id} 非 builtin")

    for model_id in models:
        if model_id.startswith("custom-local:") or model_id in auto_tiers:
            errors.append(f"模型目录内出现禁止模型: {model_id}")

    roles = model_map.get("roles", {})
    for role, rspec in sorted(roles.items()):
        check_model_id(rspec.get("modelId"), f"角色 {role}")

    policy = model_map.get("reviewPolicy", {})
    check_model_id(policy.get("defaultReviewerModelId"), "reviewPolicy.defaultReviewerModelId")

    for family, backups in model_map.get("backupModels", {}).items():
        for model_id in backups:
            check_model_id(model_id, f"backupModels.{family}")

    def scan_forbidden(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if _API_KEY_RE.search(str(key)):
                    errors.append(f"配置含疑似 API Key / Token 字段: {path}.{key}")
                scan_forbidden(value, f"{path}.{key}")
        elif isinstance(node, list):
            for idx, value in enumerate(node):
                scan_forbidden(value, f"{path}[{idx}]")
        elif isinstance(node, str):
            if _URL_RE.search(node):
                errors.append(f"配置含外部 URL: {path}={node}")

    scan_forbidden(catalog, "model-catalog")
    scan_forbidden(model_map, "model-map")

    agent_dirs, agent_found, agent_missing = _agent_definition_status(model_map)
    if agent_missing:
        warnings.append(
            "未找到子代理定义文件: " + ", ".join(agent_missing) +
            "（可按 docs/INSTALL.md 安装后重跑 preflight）")

    result: Dict[str, Any] = {
        "ok": not errors,
        "catalogModels": len(models),
        "rolesChecked": len(roles),
        "policyVersion": model_map.get("version"),
        "agentDefinitionDirs": [str(d) for d in agent_dirs],
        "agentDefinitionsFound": len(agent_found),
        "agentDefinitionsMissing": len(agent_missing),
        "warnings": warnings,
    }
    if errors:
        result["errors"] = errors
    return result


def cmd_start(args: argparse.Namespace) -> Dict[str, Any]:
    """创建运行（相同 mode + goal + 相同 source 哈希时幂等复用原 run-id）。"""
    catalog, model_map = load_configs(Path(args.config))
    mode_cfg = model_map.get("modes", {}).get(args.mode)
    if not mode_cfg:
        raise XiezhiError(f"未知模式: {args.mode}（可用: {', '.join(model_map.get('modes', {}))}）")

    sources = hash_source_files(args.source or [])
    idem_key = compute_idempotency_key(args.goal, args.mode, sources)
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)

    # 幂等查找：相同 mode、goal 与 source 哈希返回原 run-id，不重复创建
    for child in sorted(root.iterdir()):
        spec_path = child / "task-spec.json"
        if not spec_path.is_file():
            continue
        try:
            previous = read_json(spec_path)
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(previous, dict) and previous.get("idempotencyKey") == idem_key:
            current_stage: Optional[str] = None
            ledger_path = child / "run-ledger.json"
            if ledger_path.is_file():
                try:
                    current_stage = read_json(ledger_path).get("currentStage")
                except (json.JSONDecodeError, OSError):
                    current_stage = None
            return {
                "ok": True, "runId": previous.get("id"), "idempotent": True,
                "runDir": str(child.resolve()), "mode": previous.get("mode"),
                "currentStage": current_stage,
            }

    run_id = f"{_utc_stamp()}-{idem_key[:8]}"
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    now = _utc_iso()

    task_spec = {
        "id": run_id, "goal": args.goal, "mode": args.mode, "sources": sources,
        "createdAt": now, "idempotencyKey": idem_key,
        "policyVersion": model_map.get("version", "unknown"),
    }
    atomic_write_json(run_dir / "task-spec.json", task_spec)

    stages: Dict[str, Dict[str, str]] = {}
    for stage in ALL_STAGES:
        if stage in mode_cfg.get("stages", []):
            stages[stage] = {"status": "pending"}
        else:
            stages[stage] = {"status": "not_run", "reason": f"{args.mode} 模式不包含该阶段"}
    ledger = {
        "runId": run_id, "mode": args.mode,
        "currentStage": mode_cfg.get("stages", [ALL_STAGES[0]])[0],
        "stages": stages, "revisionCount": 0,
        "maxRevisions": mode_cfg.get("maxRevisions", 0),
        "createdAt": now, "updatedAt": now,
    }
    atomic_write_json(run_dir / "run-ledger.json", ledger)
    for sub in ("tickets", "receipts", "findings", "artifacts", "revisions"):
        (run_dir / sub).mkdir(exist_ok=True)

    return {
        "ok": True, "runId": run_id, "idempotent": False,
        "runDir": str(run_dir.resolve()), "mode": args.mode,
        "currentStage": ledger["currentStage"], "sourceCount": len(sources),
    }


def cmd_ticket(args: argparse.Namespace) -> Dict[str, Any]:
    """为某阶段开一张执行票据（绑定角色、模型与来源哈希）。"""
    catalog, model_map = load_configs(Path(args.config))
    run_dir = resolve_run_dir(Path(args.root), args.run_id)
    task_spec, ledger = load_run_state(run_dir)

    stage = args.stage
    if stage not in ALL_STAGES:
        raise XiezhiError(f"不存在的阶段: {stage}（可用: {', '.join(ALL_STAGES)}）")
    state = ledger.get("stages", {}).get(stage)
    if state is None:
        raise XiezhiError(f"阶段 {stage} 不在账本中")
    if state.get("status") == "not_run":
        raise XiezhiError(f"阶段 {stage} 在 {ledger.get('mode')} 模式下不适用（not_run），不允许开票")
    if state.get("status") == "passed":
        raise XiezhiError(f"阶段 {stage} 已 passed，不允许再开票")

    rspec = model_map.get("roles", {}).get(args.role)
    if rspec is None:
        raise XiezhiError(
            f"未知角色: {args.role}（可用: {', '.join(sorted(model_map.get('roles', {})))}）")
    model_id = rspec.get("modelId", "")
    entry = catalog.get("models", {}).get(model_id)
    if entry is None or not entry.get("builtin"):
        raise XiezhiError(f"角色 {args.role} 绑定模型 {model_id} 不在目录或非 builtin")

    if args.source:
        source_refs = hash_source_files(args.source)
    else:
        source_refs = list(task_spec.get("sources", []))

    stage_list = mode_stage_list(model_map, ledger.get("mode", ""))
    dependencies = stage_list[: stage_list.index(stage)] if stage in stage_list else []

    tickets_dir = run_dir / "tickets"
    existing = collect_tickets(run_dir)
    number = sum(1 for t in existing if t.get("stage") == stage) + 1
    ticket_id = f"{stage}-{number}"
    while (tickets_dir / f"{ticket_id}.json").is_file():
        number += 1
        ticket_id = f"{stage}-{number}"

    ticket = {
        "ticketId": ticket_id, "runId": args.run_id, "stage": stage, "role": args.role,
        "agentName": rspec.get("agentName", ""), "requestedModelId": model_id,
        "family": rspec.get("family", ""), "sourceRefs": source_refs,
        "criteria": args.criteria or "", "allowedTools": list(rspec.get("tools", [])),
        "outputPath": str(Path(args.output).resolve()) if args.output else None,
        "dependencies": dependencies, "createdAt": _utc_iso(),
    }
    ticket_path = tickets_dir / f"{ticket_id}.json"
    atomic_write_json(ticket_path, ticket)

    return {
        "ok": True, "ticketId": ticket_id, "ticketPath": str(ticket_path.resolve()),
        "stage": stage, "role": args.role, "agentName": ticket["agentName"],
        "requestedModelId": model_id, "family": ticket["family"],
        "sourceCount": len(source_refs), "dependencies": dependencies,
    }


def cmd_receipt(args: argparse.Namespace) -> Dict[str, Any]:
    """为票据登记回执与模型证据；passed 校验严格，不合格自动降级 unknown。"""
    run_dir = resolve_run_dir(Path(args.root), args.run_id)
    ticket_path = Path(args.ticket)
    if not ticket_path.is_file():
        alt = run_dir / "tickets" / f"{args.ticket}.json"
        if alt.is_file():
            ticket_path = alt
        else:
            raise XiezhiError(f"票据文件不存在: {args.ticket}")
    ticket = read_json(ticket_path)
    if not isinstance(ticket, dict):
        raise XiezhiError(f"票据结构非法: {ticket_path}")
    if ticket.get("runId") != args.run_id:
        raise XiezhiError(f"票据 {ticket.get('ticketId')} 不属于运行 {args.run_id}")

    ticket_id = ticket.get("ticketId") or ticket_path.stem
    status = args.status
    actual_model = args.actual_model or ""
    notes: List[str] = []
    if status == "passed":
        if not actual_model:
            status = "unknown"
            notes.append("声明 passed 但缺少 --actual-model（实际模型证据），自动降级为 unknown")
        elif actual_model != ticket.get("requestedModelId"):
            status = "unknown"
            notes.append(
                f"actual-model={actual_model} 与 requestedModelId="
                f"{ticket.get('requestedModelId')} 不一致，禁止标记 passed，降级为 unknown")

    raw_output: Optional[Dict[str, Any]] = None
    if args.raw_output:
        path = Path(args.raw_output)
        raw_output = {
            "path": str(path.resolve()),
            "sha256": sha256_file(path) if path.is_file() else None,
        }
        if not path.is_file():
            notes.append(f"原始输出文件不存在: {path}")

    artifacts: List[Dict[str, Any]] = []
    for raw in args.artifact or []:
        path = Path(raw)
        artifacts.append({
            "path": str(path.resolve()),
            "sha256": sha256_file(path) if path.is_file() else None,
        })
        if not path.is_file():
            notes.append(f"产物文件不存在: {path}")

    receipt = {
        "receiptId": ticket_id, "ticketId": ticket_id, "runId": args.run_id,
        "stage": ticket.get("stage"), "role": ticket.get("role"),
        "agentName": ticket.get("agentName"), "family": ticket.get("family"),
        "requestedModelId": ticket.get("requestedModelId"),
        "claimedStatus": args.status, "status": status,
        "actualModel": actual_model or None,
        "modelEvidence": args.model_evidence or "",
        "agentId": args.agent_id or "", "taskId": args.task_id or "",
        "rawOutput": raw_output, "artifacts": artifacts,
        "findings": list(args.finding or []), "notes": notes,
        "createdAt": _utc_iso(),
    }
    receipt_path = run_dir / "receipts" / f"{ticket_id}.json"
    atomic_write_json(receipt_path, receipt)

    return {
        "ok": True, "receiptId": ticket_id, "status": status,
        "downgraded": status != args.status,
        "receiptPath": str(receipt_path.resolve()), "notes": notes,
    }


def cmd_advance(args: argparse.Namespace) -> Dict[str, Any]:
    """推进阶段：全部票据回执 passed 且来源/产物哈希一致才允许。"""
    catalog, model_map = load_configs(Path(args.config))
    run_dir = resolve_run_dir(Path(args.root), args.run_id)
    task_spec, ledger = load_run_state(run_dir)

    stage = args.stage
    if stage not in ledger.get("stages", {}):
        raise XiezhiError(f"不存在的阶段: {stage}（可用: {', '.join(ALL_STAGES)}）")
    if ledger["stages"][stage].get("status") == "not_run":
        raise XiezhiError(f"阶段 {stage} 在 {ledger.get('mode')} 模式下不适用")

    tickets = [t for t in collect_tickets(run_dir) if t.get("stage") == stage]
    if not tickets:
        raise XiezhiError(f"阶段 {stage} 没有票据，无法推进")

    receipts_map = {r.get("ticketId"): r for r in collect_receipts(run_dir)}
    problems: List[str] = []
    any_failed = False
    for ticket in tickets:
        ticket_id = ticket.get("ticketId")
        receipt = receipts_map.get(ticket_id)
        if receipt is None:
            problems.append(f"票据 {ticket_id} 没有对应回执")
            continue
        if receipt.get("status") != "passed":
            problems.append(
                f"票据 {ticket_id} 回执状态为 {receipt.get('status')}（必须全部 passed 才能推进）")
            if receipt.get("status") == "failed":
                any_failed = True
        for ref in ticket.get("sourceRefs", []):
            path = Path(str(ref.get("path", "")))
            current = sha256_file(path) if path.is_file() else None
            if current != ref.get("sha256"):
                problems.append(f"来源哈希不一致或文件缺失: {ref.get('path')}")
                any_failed = True
        if receipt.get("status") == "passed":
            evidences = list(receipt.get("artifacts") or [])
            if receipt.get("rawOutput"):
                evidences.append(receipt["rawOutput"])
            for evidence in evidences:
                path = Path(str(evidence.get("path", "")))
                current = sha256_file(path) if path.is_file() else None
                if current != evidence.get("sha256"):
                    problems.append(f"产物哈希不一致或文件缺失: {evidence.get('path')}")
                    any_failed = True

    if problems:
        new_status = "failed" if any_failed else "unknown"
        ledger["stages"][stage] = {"status": new_status, "reason": "; ".join(problems)[:500]}
        save_ledger(run_dir, ledger)
        raise XiezhiError(
            f"阶段 {stage} 推进失败，状态置为 {new_status}",
            data={"runId": args.run_id, "stage": stage,
                  "status": new_status, "problems": problems})

    state = ledger["stages"][stage]
    state["status"] = "passed"
    state.pop("reason", None)
    state["passedAt"] = _utc_iso()
    stage_list = mode_stage_list(model_map, ledger.get("mode", ""))
    ledger["currentStage"] = first_pending(ledger, stage_list)
    save_ledger(run_dir, ledger)
    return {
        "ok": True, "runId": args.run_id, "stage": stage, "status": "passed",
        "currentStage": ledger["currentStage"],
    }


def cmd_reviewer(args: argparse.Namespace) -> Dict[str, Any]:
    """推荐独立复核角色：优先选择家族不在作者集合中的候选。"""
    catalog, model_map = load_configs(Path(args.config))
    run_dir = resolve_run_dir(Path(args.root), args.run_id)
    task_spec, ledger = load_run_state(run_dir)

    receipts = collect_receipts(run_dir)
    author_families = sorted({
        r.get("family") for r in receipts
        if r.get("stage") in REVIEW_INPUT_STAGES
        and r.get("status") == "passed" and r.get("family")
    })
    policy = model_map.get("reviewPolicy", {})
    roles = model_map.get("roles", {})
    mode_review_level = model_map.get("modes", {}).get(ledger.get("mode"), {}).get("reviewLevel")

    chosen: Optional[str] = None
    for role in policy.get("candidateRoleOrder", []):
        rspec = roles.get(role)
        if rspec and rspec.get("family") not in author_families:
            chosen = role
            break

    if chosen:
        rspec = roles[chosen]
        return {
            "ok": True, "runId": args.run_id, "authorFamilies": author_families,
            "recommendedRole": chosen, "recommendedModelId": rspec.get("modelId"),
            "recommendedAgentName": rspec.get("agentName"), "family": rspec.get("family"),
            "independenceLevel": "cross_family_review", "fallback": False,
            "modeReviewLevel": mode_review_level,
        }
    fallback_role = policy.get("defaultReviewerRole", "business_reviewer")
    rspec = roles.get(fallback_role, {})
    return {
        "ok": True, "runId": args.run_id, "authorFamilies": author_families,
        "recommendedRole": fallback_role,
        "recommendedModelId": policy.get("defaultReviewerModelId") or rspec.get("modelId"),
        "recommendedAgentName": rspec.get("agentName"), "family": rspec.get("family"),
        "independenceLevel": "context_review", "fallback": True,
        "modeReviewLevel": mode_review_level,
        "disclosure": "无跨家族候选复核者，降级为 context_review（新上下文内复核），不宣称 cross_family_review",
    }


def cmd_revise(args: argparse.Namespace) -> Dict[str, Any]:
    """发起一轮返修：受模式最大轮次限制，重置执行链阶段并保留前置证据。"""
    catalog, model_map = load_configs(Path(args.config))
    run_dir = resolve_run_dir(Path(args.root), args.run_id)
    task_spec, ledger = load_run_state(run_dir)

    mode = ledger.get("mode", "")
    mode_cfg = model_map.get("modes", {}).get(mode, {})
    max_revisions = int(mode_cfg.get("maxRevisions", 0))
    current_count = int(ledger.get("revisionCount", 0))
    if current_count >= max_revisions:
        raise XiezhiError(
            f"{mode} 模式最大返修轮次为 {max_revisions}，当前已完成 {current_count} 轮，拒绝返修")

    round_number = current_count + 1
    previous_findings: List[Dict[str, Any]] = []
    for receipt in collect_receipts(run_dir):
        for finding in receipt.get("findings") or []:
            previous_findings.append({
                "receiptId": receipt.get("receiptId"),
                "stage": receipt.get("stage"),
                "finding": finding,
            })
    new_key = hashlib.sha256(
        f"{task_spec.get('idempotencyKey')}#round-{round_number}#{args.reason}".encode("utf-8")
    ).hexdigest()

    reset_stages: List[str] = []
    for stage in REVISE_RESET_STAGES:
        state = ledger.get("stages", {}).get(stage)
        if state and state.get("status") != "not_run":
            ledger["stages"][stage] = {
                "status": "pending", "resetAt": _utc_iso(), "resetByRevision": round_number,
            }
            reset_stages.append(stage)

    revision = {
        "round": round_number, "runId": args.run_id, "reason": args.reason,
        "previousFindings": previous_findings, "newIdempotencyKey": new_key,
        "resetStages": reset_stages,
        "preservedStages": [s for s in ("coordinate", "analyze", "route")],
        "createdAt": _utc_iso(),
    }
    revision_path = run_dir / "revisions" / f"round-{round_number}.json"
    atomic_write_json(revision_path, revision)

    ledger["revisionCount"] = round_number
    stage_list = mode_stage_list(model_map, mode)
    ledger["currentStage"] = first_pending(ledger, stage_list)
    save_ledger(run_dir, ledger)

    return {
        "ok": True, "runId": args.run_id, "round": round_number,
        "resetStages": reset_stages, "revisionCount": round_number,
        "maxRevisions": max_revisions, "currentStage": ledger["currentStage"],
        "revisionPath": str(revision_path.resolve()),
    }


def cmd_verify(args: argparse.Namespace) -> Dict[str, Any]:
    """一致性校验：阶段证据、来源/产物哈希、返修上限、复核独立性。"""
    catalog, model_map = load_configs(Path(args.config))
    run_dir = resolve_run_dir(Path(args.root), args.run_id)
    task_spec, ledger = load_run_state(run_dir)

    tickets = collect_tickets(run_dir)
    receipts = collect_receipts(run_dir)
    receipts_map = {r.get("ticketId"): r for r in receipts}
    checks: List[Dict[str, str]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    # 1) 阶段证据：passed 阶段必须全部有通过回执（unknown/failed 不得标成 passed）
    stage_list = mode_stage_list(model_map, ledger.get("mode", ""))
    for stage in stage_list:
        state = ledger.get("stages", {}).get(stage, {})
        if state.get("status") != "passed":
            continue
        stage_tickets = [t for t in tickets if t.get("stage") == stage]
        stage_receipts = [receipts_map.get(t.get("ticketId")) for t in stage_tickets]
        missing = [t.get("ticketId") for t, r in zip(stage_tickets, stage_receipts) if r is None]
        not_passed = [r.get("receiptId") for r in stage_receipts
                      if r is not None and r.get("status") != "passed"]
        if not stage_tickets:
            add(f"stage_evidence:{stage}", "failed", "阶段标记 passed 但没有任何票据")
        elif missing or not_passed:
            add(f"stage_evidence:{stage}", "failed",
                f"阶段标记 passed 但存在缺失回执 {missing} 或未通过回执 {not_passed}")
        else:
            add(f"stage_evidence:{stage}", "passed", f"{len(stage_tickets)} 张票据全部通过")

    # 2) 来源哈希
    changed_sources: List[str] = []
    for source in task_spec.get("sources", []):
        path = Path(str(source.get("path", "")))
        current = sha256_file(path) if path.is_file() else None
        if current != source.get("sha256"):
            changed_sources.append(str(source.get("path")))
    add("source_integrity",
        "passed" if not changed_sources else "failed",
        "全部来源哈希一致" if not changed_sources else f"来源哈希变化或缺失: {changed_sources}")

    # 3) 产物与原始输出哈希
    artifact_problems: List[str] = []
    for receipt in receipts:
        evidences = list(receipt.get("artifacts") or [])
        if receipt.get("rawOutput"):
            evidences.append(receipt["rawOutput"])
        for evidence in evidences:
            if not evidence.get("sha256"):
                artifact_problems.append(
                    f"{receipt.get('receiptId')}: {evidence.get('path')} 无哈希（文件缺失）")
                continue
            path = Path(str(evidence.get("path", "")))
            current = sha256_file(path) if path.is_file() else None
            if current != evidence.get("sha256"):
                artifact_problems.append(
                    f"{receipt.get('receiptId')}: {evidence.get('path')} 哈希不一致或缺失")
    add("artifact_integrity",
        "passed" if not artifact_problems else "failed",
        "全部产物与原始输出哈希一致" if not artifact_problems else "; ".join(artifact_problems))

    # 4) 返修上限
    revision_count = int(ledger.get("revisionCount", 0))
    max_revisions = int(ledger.get(
        "maxRevisions",
        model_map.get("modes", {}).get(ledger.get("mode"), {}).get("maxRevisions", 0)))
    add("revision_limit",
        "passed" if revision_count <= max_revisions else "failed",
        f"返修 {revision_count}/{max_revisions} 轮")

    # 5) 复核独立性与作者家族一致性
    author_families = {
        r.get("family") for r in receipts
        if r.get("stage") in REVIEW_INPUT_STAGES
        and r.get("status") == "passed" and r.get("family")
    }
    review_state = ledger.get("stages", {}).get("review", {})
    if review_state.get("status") == "passed":
        review_families = {
            r.get("family") for r in receipts
            if r.get("stage") == "review" and r.get("family")
        }
        overlap = sorted(f for f in review_families if f in author_families)
        review_level = model_map.get("modes", {}).get(
            ledger.get("mode"), {}).get("reviewLevel", "")
        if overlap and review_level == "cross_family_review_if_possible":
            add("review_independence", "failed",
                f"复核家族 {overlap} 与作者家族重叠，与跨家族复核声明矛盾")
        elif overlap:
            add("review_independence", "warning",
                f"复核家族 {overlap} 与作者家族重叠；当前模式复核级别为 {review_level}，按同家族披露")
        else:
            add("review_independence", "passed", "复核家族与作者家族无重叠，独立性声明成立")
    else:
        add("review_independence", "not_applicable", "review 阶段尚未 passed")

    ok = all(c["status"] != "failed" for c in checks)
    return {
        "ok": ok, "runId": args.run_id, "mode": ledger.get("mode"),
        "currentStage": ledger.get("currentStage"),
        "revisionCount": revision_count, "checks": checks,
    }


# ---------------------------------------------------------------------------
# HTML 报告
# ---------------------------------------------------------------------------

_STATUS_LABEL = {
    "pending": "进行中", "passed": "已通过", "failed": "失败",
    "unknown": "未知", "not_run": "未运行",
}

_REPORT_CSS = """<style>
body{font-family:"Microsoft YaHei","PingFang SC","Segoe UI",sans-serif;max-width:1100px;margin:0 auto;padding:24px;color:#1f2933;line-height:1.6}
h1{border-bottom:3px solid #2440a8;padding-bottom:8px}
h2{margin-top:28px;border-left:5px solid #2440a8;padding-left:10px}
table{border-collapse:collapse;width:100%;margin:10px 0;font-size:14px}
th,td{border:1px solid #c8ced6;padding:6px 10px;text-align:left;vertical-align:top;word-break:break-all}
th{background:#eef1f7}
.hash{font-family:Consolas,monospace;font-size:12px}
.status{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px}
.st-passed{background:#e2f2e4;color:#1c6b2c}
.st-failed{background:#fbe3e3;color:#a11c1c}
.st-unknown{background:#fff1dc;color:#8a5a00}
.st-pending{background:#e8f0fb;color:#1f4e9c}
.st-not_run{background:#ececec;color:#555}
.goal{background:#f6f8fa;border:1px solid #d7dce3;padding:10px 14px}
.ok{color:#1c6b2c;font-weight:bold}
.bad{color:#a11c1c;font-weight:bold}
footer{margin-top:36px;border-top:1px solid #c8ced6;padding-top:10px;color:#666;font-size:12px}
@media print{body{max-width:none;padding:0}section{page-break-inside:avoid}}
</style>"""


def _build_report_html(
    task_spec: Dict[str, Any],
    ledger: Dict[str, Any],
    tickets: List[Dict[str, Any]],
    receipts: List[Dict[str, Any]],
    revisions: List[Dict[str, Any]],
    model_map: Dict[str, Any],
    source_rows: List[Tuple[str, str, str]],
) -> str:
    """生成自包含、可打印的 HTML 报告文档（无外部 CSS/JS 引用）。"""

    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value))

    parts: List[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="zh-CN"><head><meta charset="utf-8">')
    parts.append(f"<title>协智引擎运行报告 {esc(task_spec.get('id'))}</title>")
    parts.append(_REPORT_CSS)
    parts.append("</head><body>")
    parts.append("<h1>协智引擎运行报告</h1>")
    parts.append(
        f"<p class='meta'>run-id: <strong>{esc(task_spec.get('id'))}</strong> · "
        f"模式: {esc(ledger.get('mode'))} · policyVersion: {esc(task_spec.get('policyVersion'))} · "
        f"创建: {esc(task_spec.get('createdAt'))} · 报告生成: {esc(_utc_iso())}</p>")

    # 目标
    parts.append("<section><h2>目标</h2>")
    parts.append(f"<p class='goal'>{esc(task_spec.get('goal'))}</p></section>")

    # 边界声明
    parts.append("<section><h2>边界声明</h2><ul>")
    for item in [
        "本报告由本地脚本 runtime/xiezhi.py 生成，全程未调用任何外部模型 API。",
        "仅使用 WorkBuddy 内置积分模型；无 API Key、无供应商 SDK、无自定义端点、"
        "无 custom-local 模型与 Auto/fast/balanced/deep 自动档。",
        "缺少实际模型证据（actual-model）的回执一律标记 unknown，不计为通过。",
        "不静默替换模型；模型不可用时停止并请求用户确认。",
        "模型分工、阶段与返修策略以 config/model-map.json（policyVersion）为准。",
    ]:
        parts.append(f"<li>{esc(item)}</li>")
    parts.append("</ul></section>")

    # 模型分工
    roles = model_map.get("roles", {})
    parts.append(
        "<section><h2>模型分工</h2><table><thead><tr>"
        "<th>角色</th><th>子代理</th><th>模型</th><th>家族</th><th>职责</th>"
        "</tr></thead><tbody>")
    for role in sorted(roles):
        rspec = roles[role]
        parts.append(
            f"<tr><td>{esc(role)}</td><td>{esc(rspec.get('agentName'))}</td>"
            f"<td>{esc(rspec.get('modelId'))}</td><td>{esc(rspec.get('family'))}</td>"
            f"<td>{esc(rspec.get('assignment'))}</td></tr>")
    parts.append("</tbody></table></section>")

    # 阶段状态
    parts.append(
        "<section><h2>阶段状态</h2><table><thead><tr>"
        "<th>阶段</th><th>状态</th><th>说明</th></tr></thead><tbody>")
    for stage in ALL_STAGES:
        state = ledger.get("stages", {}).get(stage, {})
        status = state.get("status", "unknown")
        label = _STATUS_LABEL.get(status, status)
        reason = state.get("reason") or ""
        if status == "passed" and state.get("passedAt"):
            reason = f"passedAt {state['passedAt']}"
        parts.append(
            f"<tr><td>{esc(stage)}</td>"
            f"<td><span class='status st-{esc(status)}'>{esc(label)}</span></td>"
            f"<td>{esc(reason)}</td></tr>")
    parts.append("</tbody></table></section>")

    # 未运行项
    not_run = [
        (stage, ledger.get("stages", {}).get(stage, {}).get("reason", ""))
        for stage in ALL_STAGES
        if ledger.get("stages", {}).get(stage, {}).get("status") == "not_run"
    ]
    parts.append("<section><h2>未运行项</h2>")
    if not_run:
        parts.append("<ul>" + "".join(
            f"<li>{esc(stage)}：{esc(reason)}</li>" for stage, reason in not_run) + "</ul>")
    else:
        parts.append("<p>（无：当前模式包含的阶段均已纳入流程）</p>")
    parts.append("</section>")

    # 来源
    parts.append("<section><h2>来源</h2>")
    if source_rows:
        parts.append(
            "<table><thead><tr><th>路径</th><th>SHA256</th><th>当前校验</th>"
            "</tr></thead><tbody>")
        for path, digest, verdict in source_rows:
            css = "ok" if verdict == "一致" else "bad"
            parts.append(
                f"<tr><td>{esc(path)}</td><td class='hash'>{esc(digest)}</td>"
                f"<td class='{css}'>{esc(verdict)}</td></tr>")
        parts.append("</tbody></table>")
    else:
        parts.append("<p>（本运行未登记来源文件）</p>")
    parts.append("</section>")

    # 回执与模型证据
    parts.append("<section><h2>回执与模型证据</h2>")
    if receipts:
        parts.append(
            "<table><thead><tr><th>回执</th><th>阶段</th><th>角色</th>"
            "<th>请求模型</th><th>实际模型</th><th>状态</th><th>模型证据</th>"
            "<th>agentId / taskId</th></tr></thead><tbody>")
        for receipt in receipts:
            status = receipt.get("status", "unknown")
            label = _STATUS_LABEL.get(status, status)
            actual = esc(receipt.get("actualModel")) or "—"
            evidence = esc(receipt.get("modelEvidence")) or "—"
            agent = esc(receipt.get("agentId")) or "—"
            task = esc(receipt.get("taskId")) or "—"
            parts.append(
                f"<tr><td>{esc(receipt.get('receiptId'))}</td>"
                f"<td>{esc(receipt.get('stage'))}</td><td>{esc(receipt.get('role'))}</td>"
                f"<td>{esc(receipt.get('requestedModelId'))}</td><td>{actual}</td>"
                f"<td><span class='status st-{esc(status)}'>{esc(label)}</span></td>"
                f"<td>{evidence}</td><td>{agent} / {task}</td></tr>")
        parts.append("</tbody></table>")
    else:
        parts.append("<p>（尚无回执）</p>")
    parts.append("</section>")

    # 发现项
    findings = [
        (receipt.get("receiptId"), finding)
        for receipt in receipts for finding in (receipt.get("findings") or [])
    ]
    parts.append("<section><h2>发现项</h2>")
    if findings:
        parts.append("<ul>" + "".join(
            f"<li>[{esc(rid)}] {esc(finding)}</li>" for rid, finding in findings) + "</ul>")
    else:
        parts.append("<p>（无）</p>")
    parts.append("</section>")

    # 返修链
    parts.append("<section><h2>返修链</h2>")
    if revisions:
        parts.append("<ul>")
        for revision in revisions:
            reset = ", ".join(revision.get("resetStages") or [])
            parts.append(
                f"<li>第 {esc(revision.get('round'))} 轮 · {esc(revision.get('createdAt'))} · "
                f"原因：{esc(revision.get('reason'))} · 重置阶段：{esc(reset)} · "
                f"保留发现 {len(revision.get('previousFindings') or [])} 项</li>")
        parts.append("</ul>")
    else:
        parts.append(
            f"<p>（无返修；当前模式最大返修轮次 {esc(ledger.get('maxRevisions'))}）</p>")
    parts.append("</section>")

    parts.append(
        f"<footer>协智引擎 WorkBuddy 原生版 · 本地生成，未联网，未调用外部模型 API · "
        f"run-id {esc(task_spec.get('id'))}</footer>")
    parts.append("</body></html>")
    return "\n".join(parts)


def cmd_report(args: argparse.Namespace) -> Dict[str, Any]:
    """生成 report.html（自包含、可打印、无外部资源引用）。"""
    catalog, model_map = load_configs(Path(args.config))
    run_dir = resolve_run_dir(Path(args.root), args.run_id)
    task_spec, ledger = load_run_state(run_dir)
    tickets = collect_tickets(run_dir)
    receipts = collect_receipts(run_dir)
    revisions_dir = run_dir / "revisions"
    revisions: List[Dict[str, Any]] = []
    if revisions_dir.is_dir():
        revisions = sorted(
            (read_json(p) for p in revisions_dir.glob("*.json")),
            key=lambda r: int(r.get("round", 0)),
        )

    source_rows: List[Tuple[str, str, str]] = []
    for source in task_spec.get("sources", []):
        path = Path(str(source.get("path", "")))
        current = sha256_file(path) if path.is_file() else None
        if current == source.get("sha256"):
            verdict = "一致"
        elif current is None:
            verdict = "缺失"
        else:
            verdict = "不一致"
        source_rows.append((str(source.get("path")), str(source.get("sha256")), verdict))

    document = _build_report_html(
        task_spec, ledger, tickets, receipts, revisions, model_map, source_rows)
    report_path = run_dir / "report.html"
    atomic_write_text(report_path, document)
    return {
        "ok": True, "runId": args.run_id,
        "reportPath": str(report_path.resolve()),
        "bytes": len(document.encode("utf-8")),
    }


def cmd_status(args: argparse.Namespace) -> Dict[str, Any]:
    """输出当前阶段、阶段状态、未解决发现与返修次数。"""
    run_dir = resolve_run_dir(Path(args.root), args.run_id)
    task_spec, ledger = load_run_state(run_dir)
    receipts = collect_receipts(run_dir)
    unresolved: List[Dict[str, Any]] = []
    for receipt in receipts:
        if receipt.get("status") == "passed":
            continue
        for finding in receipt.get("findings") or []:
            unresolved.append({
                "receiptId": receipt.get("receiptId"),
                "stage": receipt.get("stage"),
                "finding": finding,
            })
    return {
        "ok": True, "runId": args.run_id, "mode": ledger.get("mode"),
        "currentStage": ledger.get("currentStage"),
        "stages": {
            stage: ledger.get("stages", {}).get(stage, {}).get("status")
            for stage in ALL_STAGES
        },
        "unresolvedFindings": unresolved, "unresolvedCount": len(unresolved),
        "revisionCount": ledger.get("revisionCount", 0),
        "maxRevisions": ledger.get("maxRevisions"),
        "updatedAt": ledger.get("updatedAt"),
    }


def cmd_selftest(args: argparse.Namespace) -> Dict[str, Any]:
    """调用 unittest 测试集自检；测试文件不存在时给出明确状态而不崩溃。"""
    tests_dir = (SCRIPT_DIR / ".." / "tests").resolve()
    command = "python -m unittest discover -s ../tests -p test_*.py"
    has_tests = tests_dir.is_dir() and any(tests_dir.glob("test_*.py"))
    if not has_tests:
        return {
            "ok": True, "status": "no_tests", "command": command,
            "testsDir": str(tests_dir),
            "detail": f"测试目录 {tests_dir} 不存在或没有 test_*.py 文件；"
                      "跳过 unittest，引擎自检未崩溃。",
        }
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover",
             "-s", "../tests", "-p", "test_*.py"],
            cwd=str(SCRIPT_DIR), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "status": "error", "command": command,
            "detail": "unittest 执行超时（>300 秒）",
        }
    except OSError as exc:
        return {
            "ok": False, "status": "error", "command": command,
            "detail": f"无法启动 unittest: {exc}",
        }
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return {
        "ok": proc.returncode == 0,
        "status": "passed" if proc.returncode == 0 else "failed",
        "command": command,
        "returncode": proc.returncode,
        "outputTail": output[-2000:],
    }


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--root", default=str(DEFAULT_ROOT),
        help="运行账本根目录（默认: 当前工作目录/runs）")
    common.add_argument(
        "--config", default=str(DEFAULT_CONFIG_DIR),
        help="配置目录（默认: 脚本上级目录/config）")

    parser = argparse.ArgumentParser(
        prog="xiezhi.py",
        description="协智引擎 WorkBuddy 原生版本地运行时（仅标准库，不联网，不调用模型 API）")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    sub.add_parser("preflight", parents=[common],
                   help="预检模型目录、映射与子代理定义")

    p = sub.add_parser("start", parents=[common], help="创建（或幂等复用）一次运行")
    p.add_argument("--goal", required=True, help="任务目标描述")
    p.add_argument("--mode", required=True, choices=["fast", "standard", "strict"],
                   help="运行模式")
    p.add_argument("--source", nargs="+", default=[], help="来源文件路径（可多个）")

    p = sub.add_parser("ticket", parents=[common], help="为某阶段开一张执行票据")
    p.add_argument("--run-id", required=True, help="运行 ID")
    p.add_argument("--stage", required=True, help="阶段名")
    p.add_argument("--role", required=True, help="model-map 中的角色名")
    p.add_argument("--source", nargs="+", default=[],
                   help="票据来源（默认沿用 task-spec 来源）")
    p.add_argument("--criteria", default="", help="验收判据")
    p.add_argument("--output", default=None, help="预期产物路径")

    p = sub.add_parser("receipt", parents=[common], help="为票据登记回执与模型证据")
    p.add_argument("--run-id", required=True, help="运行 ID")
    p.add_argument("--ticket", required=True, help="票据文件路径或票据 ID")
    p.add_argument("--status", required=True,
                   choices=["passed", "failed", "unknown"], help="回执状态")
    p.add_argument("--agent-id", default="", help="子代理 agentId")
    p.add_argument("--task-id", default="", help="子任务 taskId")
    p.add_argument("--actual-model", default="",
                   help="实际执行模型 ID（passed 必填且须与请求一致）")
    p.add_argument("--model-evidence", default="", help="模型证据说明")
    p.add_argument("--raw-output", default=None, help="原始输出文件路径")
    p.add_argument("--artifact", nargs="+", default=[], help="产物文件路径（可多个）")
    p.add_argument("--finding", nargs="+", default=[], help="发现项（可多条）")

    p = sub.add_parser("advance", parents=[common],
                       help="推进阶段（要求全部票据回执 passed 且哈希一致）")
    p.add_argument("--run-id", required=True, help="运行 ID")
    p.add_argument("--stage", required=True, help="阶段名")

    p = sub.add_parser("reviewer", parents=[common], help="推荐独立复核角色")
    p.add_argument("--run-id", required=True, help="运行 ID")

    p = sub.add_parser("revise", parents=[common], help="发起一轮返修")
    p.add_argument("--run-id", required=True, help="运行 ID")
    p.add_argument("--reason", required=True, help="返修原因")

    p = sub.add_parser("verify", parents=[common], help="运行一致性校验")
    p.add_argument("--run-id", required=True, help="运行 ID")

    p = sub.add_parser("report", parents=[common], help="生成单文件可打印 HTML 报告")
    p.add_argument("--run-id", required=True, help="运行 ID")

    p = sub.add_parser("status", parents=[common], help="查看运行状态")
    p.add_argument("--run-id", required=True, help="运行 ID")

    sub.add_parser("selftest", parents=[common], help="运行 unittest 测试集自检")

    return parser


_HANDLERS = {
    "preflight": cmd_preflight,
    "start": cmd_start,
    "ticket": cmd_ticket,
    "receipt": cmd_receipt,
    "advance": cmd_advance,
    "reviewer": cmd_reviewer,
    "revise": cmd_revise,
    "verify": cmd_verify,
    "report": cmd_report,
    "status": cmd_status,
    "selftest": cmd_selftest,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    handler = _HANDLERS.get(args.command)
    if handler is None:
        print(json.dumps({"ok": False, "error": f"未知命令: {args.command}"},
                         ensure_ascii=False))
        return 2
    try:
        result = handler(args)
    except XiezhiError as exc:
        payload: Dict[str, Any] = {"ok": False, "error": str(exc)}
        payload.update(exc.data)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as exc:
        print(json.dumps(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
