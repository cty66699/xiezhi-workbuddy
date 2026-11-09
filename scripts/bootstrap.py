#!/usr/bin/env python3
"""One-command WorkBuddy deployment from a cloned Xiezhi Engine repository.

Installs:
- user-level Skill
- WorkBuddy and CodeBuddy named subagents
- optional Agent-type expert entry
- adapted built-in model catalog/map when local product config is discoverable

No external network access, no API keys, no destructive cleanup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
REPO = SCRIPT_DIR.parent
CONFIG_SRC = REPO / "config"
SKILL_SRC = REPO / "skill"
RUNTIME_CLI = REPO / "runtime" / "xiezhi.py"
EXPERT_SRC = REPO / "examples" / "expert-package"

AUTO_TIERS = {"Auto", "auto", "fast-model", "balanced-model", "deep-model"}

ROLE_PREFERENCES: Dict[str, List[str]] = {
    "sol_controller": ["Kimi-K3.1-Pro", "Kimi-K3", "Kimi-K2.8-Preview"],
    "requirements_analyst": ["GLM-5.3", "GLM-5.2"],
    "luna_router": ["GLM-5.3-Flash", "DeepSeek-V4.1-Flash", "DeepSeek-V4-Flash"],
    "fast_executor": ["DeepSeek-V4.1-Flash", "DeepSeek-V4-Flash", "GLM-5.3-Flash"],
    "long_context_deep_checker": ["DeepSeek-V4-Pro", "DeepSeek-V4.1-Flash"],
    "coding_executor": ["Kimi-K2.7-Code", "Kimi-K2.8-Preview", "Kimi-K3.1-Pro"],
    "office_data_executor": ["MiniMax-M3.1-Flash", "MiniMax-M3", "MiniMax-M2.7"],
    "chinese_writer": ["Hy4", "Hy3"],
    "visual_document_executor": ["Kimi-K2.6", "Kimi-K2.8-Preview", "MiniMax-M3.1-Flash"],
    "multimodal_reviewer": ["GLM-5v-Turbo", "MiniMax-M3.1-Flash", "Kimi-K2.8-Preview"],
    "business_reviewer": ["Hy4", "Hy3", "DeepSeek-V4-Pro"],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def infer_family(text: str) -> str:
    value = text.lower()
    if "kimi" in value:
        return "Moonshot Kimi"
    if "glm" in value:
        return "Zhipu GLM"
    if "deepseek" in value:
        return "DeepSeek"
    if "minimax" in value:
        return "MiniMax"
    if value.startswith("hy") or "hunyuan" in value:
        return "Tencent Hunyuan"
    return "Other"


def is_builtin_model(model: Dict[str, Any]) -> bool:
    model_id = str(model.get("id", ""))
    name = str(model.get("name", ""))
    tags = [str(tag).lower() for tag in model.get("tags", []) or []]
    if model_id.startswith("custom-local:") or "custom" in tags:
        return False
    if model_id in AUTO_TIERS or name in AUTO_TIERS:
        return False
    if model.get("disabled") is True or model.get("enabled") is False:
        return False
    return bool(model_id)


def find_product_config(config_dir: Path) -> Optional[Dict[str, Any]]:
    """Read the newest local WorkBuddy product config cache, if present."""
    spill = config_dir / "cache" / "conversation-product-spill"
    candidates: List[Path] = []
    if spill.is_dir():
        candidates.extend(sorted(spill.glob("acc-product-config-v3-*.json"),
                                 key=lambda p: p.stat().st_mtime, reverse=True))
        candidates.extend(sorted(spill.glob("*.json"),
                                 key=lambda p: p.stat().st_mtime, reverse=True))
    for candidate in candidates[:8]:
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("models"), list):
            return data
    return None


def build_discovered_catalog(product: Dict[str, Any]) -> Dict[str, Any]:
    models: Dict[str, Any] = {}
    for item in product.get("models", []):
        if not isinstance(item, dict) or not is_builtin_model(item):
            continue
        model_id = str(item["id"])
        name = str(item.get("name") or model_id)
        models[model_id] = {
            "displayName": name,
            "family": infer_family(name + " " + model_id),
            "builtin": True,
            "supportsImages": bool(item.get("supportsImages")),
            "supportsToolCall": bool(item.get("supportsToolCall")),
            "supportsReasoning": bool(item.get("supportsReasoning")),
            "maxInputTokens": item.get("maxInputTokens"),
            "strength": "Discovered from local WorkBuddy built-in model catalog",
        }
    return {
        "schema": 1,
        "generatedAt": utc_now(),
        "source": "Discovered from local WorkBuddy product model catalog",
        "policy": "Only built-in credit models; custom-local, custom API models and Auto tiers excluded",
        "models": models,
        "excluded": {
            "customLocalPrefix": "custom-local:",
            "autoTiers": sorted(AUTO_TIERS),
            "reason": "Custom models may use external API keys; Auto tiers cannot prove fixed model routing",
        },
    }


def choose_model(role: str, models: List[Dict[str, Any]], current_id: str) -> Optional[Dict[str, Any]]:
    preferences = ROLE_PREFERENCES.get(role, [])
    for preferred in preferences:
        needle = preferred.lower()
        for model in models:
            if needle in str(model.get("name", "")).lower() or needle == str(model.get("id", "")).lower():
                return model
    for model in models:
        if str(model.get("id", "")) == current_id:
            return model
    return None


def adapt_model_config(config_dir: Path, warnings: List[str]) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    """Return adapted catalog, model-map and discovery source."""
    catalog = load_json(CONFIG_SRC / "model-catalog.json")
    model_map = load_json(CONFIG_SRC / "model-map.json")
    product = find_product_config(config_dir)
    if not product:
        warnings.append("Local WorkBuddy product model cache not found; using bundled model catalog snapshot.")
        return catalog, model_map, "bundled-snapshot"

    discovered_models = [m for m in product.get("models", [])
                         if isinstance(m, dict) and is_builtin_model(m)]
    if not discovered_models:
        warnings.append("No built-in models found in local product cache; using bundled snapshot.")
        return catalog, model_map, "bundled-snapshot"

    catalog = build_discovered_catalog(product)
    for role, spec in model_map.get("roles", {}).items():
        current_id = str(spec.get("modelId", ""))
        chosen = choose_model(role, discovered_models, current_id)
        if chosen:
            spec["modelId"] = str(chosen["id"])
            spec["displayName"] = str(chosen.get("name") or chosen["id"])
            spec["family"] = infer_family(spec["displayName"] + " " + spec["modelId"])
        else:
            warnings.append(f"No preferred built-in model found for role {role}; kept {current_id}.")
    reviewer_role = model_map.get("reviewPolicy", {}).get("defaultReviewerRole", "business_reviewer")
    reviewer = model_map.get("roles", {}).get(reviewer_role)
    if reviewer:
        model_map["reviewPolicy"]["defaultReviewerModelId"] = reviewer["modelId"]

    families: Dict[str, List[str]] = {}
    for model_id, entry in catalog["models"].items():
        families.setdefault(entry["family"], []).append(model_id)
    model_map["backupModels"] = families
    return catalog, model_map, "local-product-cache"


def cli_agent_md(role: str, spec: Dict[str, Any]) -> str:
    tools = ", ".join(spec.get("tools", []))
    return f"""---
name: {spec['agentName']}
description: Xiezhi Engine {role} role; invoke explicitly by name only.
model: {spec['modelId']}
tools: {tools}
permissionMode: default
skills: xiezhi-engine
maxTurns: 16
disable-model-invocation: true
---

You are the Xiezhi Engine **{role}** specialist bound to WorkBuddy built-in model `{spec['modelId']}`.

## Assignment
{spec['assignment']}

## Rules
- Handle only the current task ticket; do not broaden scope or switch models.
- The dispatcher must pass modelId explicitly. If actual model evidence is unavailable, return unknown.
- Return results, sources, limitations and unresolved items; never fabricate tool calls.
- Do not use external model APIs, API keys, Token Plan or custom endpoints.
"""


def workbuddy_agent_md(role: str, spec: Dict[str, Any]) -> str:
    return f"""---
name: {spec['agentName']}
description: Xiezhi Engine {role} role bound to built-in model {spec['modelId']}.
displayName:
  en: "Xiezhi {role.replace('_', ' ').title()}"
  zh: "协智引擎·{role.replace('_', ' ')}"
profession:
  en: "Specialist Agent"
  zh: "专项执行专家"
agentMode: agentic
enabled: true
enabledAutoRun: true
model: {spec['modelId']}
skills: [xiezhi-engine]
maxTurns: 16
---

You are the Xiezhi Engine **{role}** specialist bound to WorkBuddy built-in model `{spec['modelId']}`.

## Core capability
- {spec['assignment']}
- Execute with minimum necessary context; no broadcast, no scope expansion.
- Return sources, limitations, disagreements and unresolved items.

## Output
1. Conclusion and artifact path.
2. Requested model, actual model evidence, agentId/taskId if provided.
3. Evidence and limitations.
4. Unresolved items.

## Notes
- Do not claim a model in the prompt as proof; return unknown when unproven.
- Do not use external APIs or custom models.
- Review roles must use a fresh context, never resume an author session.
"""


def write_placeholder_png(path: Path) -> None:
    """Create a valid 512x512 solid-color PNG using only the standard library."""
    width = height = 512
    color = bytes((30, 80, 160))
    raw = b"".join(b"\x00" + color * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data +
                struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n" +
           chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
           chunk(b"IDAT", zlib.compress(raw, 9)) +
           chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def install_skill(config_dir: Path, catalog: Dict[str, Any], model_map: Dict[str, Any]) -> Path:
    skill_dir = config_dir / "skills" / "xiezhi-engine"
    skill_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SKILL_SRC / "SKILL.md", skill_dir / "SKILL.md")
    copy_tree(SKILL_SRC / "references", skill_dir / "references")
    copy_tree(REPO / "docs", skill_dir / "docs")
    (skill_dir / "config").mkdir(parents=True, exist_ok=True)
    write_json(skill_dir / "config" / "model-catalog.json", catalog)
    write_json(skill_dir / "config" / "model-map.json", model_map)
    (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(RUNTIME_CLI, skill_dir / "scripts" / "xiezhi.py")
    shutil.copy2(SCRIPT_DIR / "bootstrap.py", skill_dir / "scripts" / "bootstrap.py")
    shutil.copy2(REPO / "README.md", skill_dir / "README.md")
    shutil.copy2(REPO / "LICENSE", skill_dir / "LICENSE")
    shutil.copy2(REPO / "SECURITY.md", skill_dir / "SECURITY.md")
    shutil.copy2(REPO / "NOTICE.md", skill_dir / "NOTICE.md")
    return skill_dir


def install_agents(model_map: Dict[str, Any], workbuddy_agent_dir: Path,
                   codebuddy_agent_dir: Path, workspace: Optional[Path]) -> List[str]:
    installed: List[str] = []
    for directory in [workbuddy_agent_dir, codebuddy_agent_dir]:
        directory.mkdir(parents=True, exist_ok=True)
    if workspace:
        (workspace / ".codebuddy" / "agents").mkdir(parents=True, exist_ok=True)
    for role, spec in model_map.get("roles", {}).items():
        filename = f"{spec['agentName']}.md"
        (workbuddy_agent_dir / filename).write_text(workbuddy_agent_md(role, spec), encoding="utf-8")
        cli_md = cli_agent_md(role, spec)
        (codebuddy_agent_dir / filename).write_text(cli_md, encoding="utf-8")
        if workspace:
            (workspace / ".codebuddy" / "agents" / filename).write_text(cli_md, encoding="utf-8")
        installed.append(spec["agentName"])
    return installed


def install_expert(config_dir: Path) -> Tuple[Path, Path]:
    expert_dir = config_dir / "plugins" / "marketplaces" / "my-experts" / "plugins" / "xiezhi-engine"
    copy_tree(EXPERT_SRC, expert_dir)
    avatar = expert_dir / "avatars" / "expert.png"
    if not avatar.is_file():
        write_placeholder_png(avatar)
    marketplace = config_dir / "plugins" / "marketplaces" / "my-experts" / ".codebuddy-plugin" / "marketplace.json"
    if marketplace.is_file():
        try:
            data = load_json(marketplace)
        except json.JSONDecodeError:
            data = {"name": "my-experts", "description": "my-experts marketplace (auto-generated)", "plugins": []}
    else:
        data = {"name": "my-experts", "description": "my-experts marketplace (auto-generated)", "plugins": []}
    plugins = data.setdefault("plugins", [])
    source = "./plugins/xiezhi-engine"
    if not any(isinstance(p, dict) and (p.get("name") == "xiezhi-engine" or p.get("source") == source)
               for p in plugins):
        plugins.append({"name": "xiezhi-engine", "source": source})
    write_json(marketplace, data)
    return expert_dir, marketplace


def run_preflight(skill_dir: Path, workspace: Optional[Path], config_dir: Path,
                  codebuddy_dir: Path) -> Dict[str, Any]:
    root = (workspace or Path.cwd()) / "runs"
    env = dict(os.environ)
    env["WORKBUDDY_CONFIG_DIR"] = str(config_dir)
    env["CODEBUDDY_CONFIG_DIR"] = str(codebuddy_dir)
    proc = subprocess.run(
        [sys.executable, str(skill_dir / "scripts" / "xiezhi.py"), "preflight",
         "--config", str(skill_dir / "config"), "--root", str(root)],
        cwd=str(REPO), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env,
    )
    output = (proc.stdout or "").strip()
    payload = None
    for match in re.finditer(r"\{.*\}", output, re.DOTALL):
        try:
            candidate = json.loads(match.group(0))
            if isinstance(candidate, dict):
                payload = candidate
        except json.JSONDecodeError:
            pass
    return {
        "ok": proc.returncode == 0 and bool(payload and payload.get("ok")),
        "returncode": proc.returncode,
        "payload": payload,
        "stdoutTail": output[-2000:],
        "stderrTail": (proc.stderr or "")[-2000:],
    }


def main() -> int:
    global REPO, CONFIG_SRC, SKILL_SRC, RUNTIME_CLI, EXPERT_SRC
    parser = argparse.ArgumentParser(description="Deploy Xiezhi Engine into WorkBuddy user configuration")
    parser.add_argument("--repo", default=str(REPO), help="Cloned repository root")
    parser.add_argument("--config-dir",
                        default=os.environ.get("WORKBUDDY_CONFIG_DIR", str(Path.home() / ".workbuddy")),
                        help="WorkBuddy config directory")
    parser.add_argument("--codebuddy-dir", default=str(Path.home() / ".codebuddy"),
                        help="CodeBuddy config directory")
    parser.add_argument("--workspace", default=None,
                        help="Optional workspace for project-level .codebuddy/agents")
    parser.add_argument("--yes", action="store_true", help="Non-interactive mode (default behavior is already non-interactive)")
    parser.add_argument("--skip-expert", action="store_true", help="Skip optional desktop expert entry")
    parser.add_argument("--no-model-discovery", action="store_true", help="Use bundled model snapshot without local discovery")
    parser.add_argument("--run-tests", action="store_true", help="Run unittest suite after installation")
    args = parser.parse_args()

    REPO = Path(args.repo).resolve()
    CONFIG_SRC = REPO / "config"
    SKILL_SRC = REPO / "skill"
    RUNTIME_CLI = REPO / "runtime" / "xiezhi.py"
    EXPERT_SRC = REPO / "examples" / "expert-package"

    config_dir = Path(args.config_dir).expanduser().resolve()
    codebuddy_dir = Path(args.codebuddy_dir).expanduser().resolve()
    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else None
    warnings: List[str] = []

    required = [CONFIG_SRC / "model-catalog.json", CONFIG_SRC / "model-map.json",
                SKILL_SRC / "SKILL.md", RUNTIME_CLI]
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        print(json.dumps({"ok": False, "error": "Repository is incomplete", "missing": missing},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        return 1

    if args.no_model_discovery:
        catalog = load_json(CONFIG_SRC / "model-catalog.json")
        model_map = load_json(CONFIG_SRC / "model-map.json")
        discovery_source = "bundled-snapshot"
        warnings.append("Local model discovery disabled by flag.")
    else:
        catalog, model_map, discovery_source = adapt_model_config(config_dir, warnings)

    try:
        skill_dir = install_skill(config_dir, catalog, model_map)
        agents = install_agents(model_map, config_dir / "agents",
                                codebuddy_dir / "agents", workspace)
        expert_dir = marketplace = None
        if not args.skip_expert:
            expert_dir, marketplace = install_expert(config_dir)
        preflight = run_preflight(skill_dir, workspace, config_dir, codebuddy_dir)
        tests = None
        if args.run_tests:
            proc = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", str(REPO / "tests"),
                 "-p", "test_*.py", "-q"],
                cwd=str(REPO), capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            tests = {"ok": proc.returncode == 0, "returncode": proc.returncode,
                     "outputTail": ((proc.stdout or "") + (proc.stderr or ""))[-2000:]}
        manifest = {
            "schema": 1,
            "version": "1.1.0",
            "installedAt": utc_now(),
            "sourceRepo": "https://github.com/cty66699/xiezhi-workbuddy",
            "modelDiscovery": discovery_source,
            "configDir": str(config_dir),
            "codebuddyDir": str(codebuddy_dir),
            "workspace": str(workspace) if workspace else None,
            "skillDir": str(skill_dir),
            "agents": sorted(agents),
            "expertDir": str(expert_dir) if expert_dir else None,
            "marketplace": str(marketplace) if marketplace else None,
            "warnings": warnings,
            "preflight": preflight,
            "tests": tests,
            "files": {
                "config/model-map.json": sha256_file(skill_dir / "config" / "model-map.json"),
                "config/model-catalog.json": sha256_file(skill_dir / "config" / "model-catalog.json"),
                "scripts/xiezhi.py": sha256_file(skill_dir / "scripts" / "xiezhi.py"),
                "SKILL.md": sha256_file(skill_dir / "SKILL.md"),
            },
        }
        write_json(config_dir / "xiezhi-engine-install.json", manifest)
        result = {
            "ok": bool(preflight.get("ok")),
            "version": "1.1.0",
            "installedAt": manifest["installedAt"],
            "modelDiscovery": discovery_source,
            "skill": str(skill_dir),
            "agentsInstalled": len(agents),
            "expertInstalled": expert_dir is not None,
            "preflight": preflight.get("payload") or preflight,
            "tests": tests,
            "warnings": warnings,
            "nextSteps": [
                "Restart WorkBuddy so custom subagents and the expert entry are loaded.",
                "Search for 协智引擎 / xiezhi-engine in Expert Center.",
                "Run python scripts/verify_redaction.py in the cloned repository if you need to re-check sanitization.",
            ],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "warnings": warnings},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
