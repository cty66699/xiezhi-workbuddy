#!/usr/bin/env python3
"""Scan this repository for sensitive patterns. No network access."""
from __future__ import annotations
import re, sys
from pathlib import Path
PATTERNS = [
    ("windows_user_path", r"(?i)[CD]:[\\/]+(?:Users|CDriveMoved)"),
    ("email", r"[A-Za-z0-9._%+-]+@(?:qq\.com|agent\.qq\.com|workbuddy)"),
    ("account_id", r"(?i)\b(?:2547358413|xzsxslywsrnlywyh|cty66699)\b"),
    ("real_name", r"崔天逸"),
    ("api_key_value", r"sk-[A-Za-z0-9_-]{20,}"),
    ("provider_endpoint", r"(?i)ifeasyai|minimaxi\.com|api\.example\.com"),
    ("session_or_agent_id", r"(?i)CODEBUDDY_SESSION_ID|agent-[0-9a-f-]{36}"),
    ("real_run_id", r"20261109T090409Z-e2a91e0c"),
    ("placeholder_author", r"local@workbuddy|WorkBuddy User"),
    ("generated_avatar_filename", r"Professional_cartoon_style_ill_"),
]
TEXT_SUFFIXES = {".md", ".json", ".py", ".txt", ".html", ".yml", ".yaml", ".gitignore", ""}
root = Path(__file__).resolve().parent.parent
findings = []
for path in root.rglob("*"):
    if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
        continue
    if any(part in {"__pycache__", ".git", "runs", "dist"} for part in path.parts):
        continue
    if path.relative_to(root).as_posix() == "scripts/verify_redaction.py":
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    for name, pattern in PATTERNS:
        for match in re.finditer(pattern, text):
            findings.append({"file": str(path.relative_to(root)), "pattern": name, "match": match.group(0)[:80]})
if findings:
    print("SENSITIVE PATTERNS FOUND:")
    for item in findings:
        print(item)
    sys.exit(1)
print("REDACTION CHECK PASSED: no sensitive patterns found")
