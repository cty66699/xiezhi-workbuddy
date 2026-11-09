#!/usr/bin/env python3
"""把协智引擎安装到当前用户的 WorkBuddy / CodeBuddy 配置目录。

只写本系统自己的目录和文件，不删除、不递归清理用户文件。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT = SCRIPT_DIR.parent
CONFIG = PROJECT / "config"
SKILL_SRC = PROJECT / "skill"
RUNTIME = PROJECT / "runtime" / "xiezhi.py"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def copy_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def yaml_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def cli_agent_md(role: str, spec: Dict[str, Any]) -> str:
    tools = ", ".join(spec.get("tools", []))
    return f"""---
name: {spec['agentName']}
description: 协智引擎 {role} 角色；仅在明确任务票据中按名称显式调用。
model: {spec['modelId']}
tools: {tools}
permissionMode: default
skills: xiezhi-engine
maxTurns: 16
disable-model-invocation: true
---

你是协智引擎的 **{role}** 专家，绑定 WorkBuddy 内置积分模型 `{spec['modelId']}`。

## 职责
{spec['assignment']}

## 强制规则
- 只处理当前任务票据，不扩大范围，不自行切换模型。
- 派发方必须显式传入 modelId；若你无法确认实际模型，返回 `unknown`，不得声称通过。
- 返回结果、来源、限制和未解决项；不得伪造工具调用或模型输出。
- 不使用外部模型 API、API Key、Token Plan 或自定义端点。
- 完成后把完整结果回传给主控，由主控写入运行回执。
"""


def workbuddy_agent_md(role: str, spec: Dict[str, Any]) -> str:
    return f"""---
name: {spec['agentName']}
description: 协智引擎 {role} 角色，绑定内置积分模型 {spec['modelId']}。
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

你是协智引擎的 **{role}** 专家，绑定 WorkBuddy 内置积分模型 `{spec['modelId']}`。

## 核心能力
- {spec['assignment']}
- 按最小必要上下文执行，不广播、不扩大范围。
- 明确返回来源、限制、分歧和未解决项。

## 输出规范
1. 结论与产物路径。
2. 请求模型、实际模型证据、agentId/taskId（如宿主提供）。
3. 依据与限制。
4. 未解决项。

## 注意事项
- 不用提示词自称模型；不能证明模型时返回 `unknown`。
- 不使用外部 API 或自定义模型。
- 复核角色必须使用全新上下文，不恢复作者会话。
"""


def install(project: Path, workbuddy_config: Path) -> Dict[str, Any]:
    project = project.resolve()
    workbuddy_config = workbuddy_config.resolve()
    codebuddy_config = Path.home() / ".codebuddy"
    workspace_agents = project.parent / ".codebuddy" / "agents"

    model_map = load_json(project / "config" / "model-map.json")
    catalog = load_json(project / "config" / "model-catalog.json")
    for role, spec in model_map["roles"].items():
        model_id = spec["modelId"]
        if model_id.startswith("custom-local:") or not catalog["models"].get(model_id, {}).get("builtin"):
            raise RuntimeError(f"拒绝安装非内置积分模型: {role}={model_id}")

    target_dirs = {
        "workbuddy_agents": workbuddy_config / "agents",
        "codebuddy_agents": codebuddy_config / "agents",
        "workspace_agents": workspace_agents,
    }
    for path in target_dirs.values():
        path.mkdir(parents=True, exist_ok=True)

    for role, spec in model_map["roles"].items():
        filename = spec["agentName"] + ".md"
        write_text(target_dirs["workbuddy_agents"] / filename, workbuddy_agent_md(role, spec))
        cli_md = cli_agent_md(role, spec)
        write_text(target_dirs["codebuddy_agents"] / filename, cli_md)
        write_text(target_dirs["workspace_agents"] / filename, cli_md)

    skill_root = workbuddy_config / "skills" / "xiezhi-engine"
    skill_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(project / "skill" / "SKILL.md", skill_root / "SKILL.md")
    copy_tree(project / "skill" / "references", skill_root / "references")
    copy_tree(project / "config", skill_root / "config")
    (skill_root / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(project / "runtime" / "xiezhi.py", skill_root / "scripts" / "xiezhi.py")
    shutil.copy2(project / "README.md", skill_root / "README.md")
    shutil.copy2(project / "docs" / "INSTALL.md", skill_root / "INSTALL.md")
    shutil.copy2(project / "docs" / "USAGE.md", skill_root / "USAGE.md")
    shutil.copy2(project / "docs" / "LIMITATIONS.md", skill_root / "LIMITATIONS.md")

    manifest = {
        "schema": 1,
        "version": model_map["version"],
        "installedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project": str(project),
        "workbuddyConfig": str(workbuddy_config),
        "paths": {key: str(value) for key, value in target_dirs.items()} | {"skill": str(skill_root)},
        "agents": sorted(spec["agentName"] for spec in model_map["roles"].values()),
        "modelIds": sorted({spec["modelId"] for spec in model_map["roles"].values()}),
        "files": {},
    }
    for path in [project / "config" / "model-map.json", project / "config" / "model-catalog.json",
                 project / "runtime" / "xiezhi.py", project / "skill" / "SKILL.md"]:
        manifest["files"][str(path.relative_to(project)).replace("\\", "/")] = sha256_file(path)
    manifest_path = workbuddy_config / "xiezhi-engine-install.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    dist_dir = project / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    archive = dist_dir / "xiezhi-workbuddy.zip"
    excluded_dirs = {"__pycache__", "dist", "runs", "xiezhi-runs", ".git", ".codebuddy"}
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in project.rglob("*"):
            if not path.is_file() or any(part in excluded_dirs for part in path.parts):
                continue
            if path.suffix == ".pyc" or path == archive:
                continue
            zf.write(path, path.relative_to(project).as_posix())
    return {"ok": True, "manifest": str(manifest_path), "archive": str(archive),
            "installedAgents": len(model_map["roles"]), "skill": str(skill_root)}


def main() -> int:
    parser = argparse.ArgumentParser(description="安装协智引擎到用户级 WorkBuddy 配置")
    parser.add_argument("--project", default=str(PROJECT), help="项目源码目录")
    parser.add_argument("--workbuddy-config",
                        default=os.environ.get("WORKBUDDY_CONFIG_DIR", str(Path.home() / ".workbuddy")),
                        help="WORKBUDDY_CONFIG_DIR，默认自动读取环境变量")
    args = parser.parse_args()
    try:
        result = install(Path(args.project), Path(args.workbuddy_config))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
