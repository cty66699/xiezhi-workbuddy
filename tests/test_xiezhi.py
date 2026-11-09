"""协智引擎 runtime/xiezhi.py 的标准库集成测试。

设计约定
--------
* 全部测试通过 ``subprocess.run([sys.executable, SCRIPT, ...])`` 调用真实 CLI，
  不 import 实现模块，因此实现内部结构变化不会破坏测试。
* 每个测试使用独立的 ``tempfile.TemporaryDirectory()``，自行构造一条完整 run，
  互不依赖执行顺序，也不读写用户目录（``~/.workbuddy`` 仅由 preflight 只读探测）。
* 运行根目录始终显式传给所有子命令（规格声明默认 ``--root`` 为工作目录下的
  ``runs``）。这样即使 cwd 是项目目录，也不会把 run 写进项目里。
* 需要“注入非法配置”的用例只复制 ``config/`` 到临时目录，并通过 ``--config``
  指向副本后修改；不复制整个项目，避免无谓 I/O。
* ``runtime/xiezhi.py`` 尚未就绪时，整类测试 skip，避免误报通过。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "runtime" / "xiezhi.py"
CONFIG_DIR = PROJECT / "config"
MODEL_MAP_PATH = CONFIG_DIR / "model-map.json"
MODEL_CATALOG_PATH = CONFIG_DIR / "model-catalog.json"

READY = SCRIPT.is_file() and MODEL_MAP_PATH.is_file() and MODEL_CATALOG_PATH.is_file()

SKIP_REASON = f"实现脚本尚未就绪（缺少 {SCRIPT}）" if not SCRIPT.is_file() else "配置缺失"

# 阶段 -> 该阶段默认使用的模型角色，取自 config/model-map.json 的 roles。
STAGE_ROLE = {
    "coordinate": "sol_controller",
    "analyze": "requirements_analyst",
    "route": "luna_router",
    "execute": "fast_executor",
    "collect": "luna_router",
    "safety": "sol_controller",
    "review": "business_reviewer",
    "adjudicate": "sol_controller",
}

RUN_ID_KEYS = ("runId", "run-id", "run_id", "id")
TICKET_ID_KEYS = ("ticketId", "ticket-id", "id")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_json_objects(text: str):
    """从混合了日志的 stdout 中按出现顺序产出平衡的 JSON 对象文本。"""
    depth = 0
    start = None
    in_string = False
    escaped = False
    for index, char in enumerate(text):
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
        elif char == "}":
            if depth:
                depth -= 1
                if depth == 0 and start is not None:
                    yield text[start : index + 1]
                    start = None


class CliResult:
    def __init__(self, proc: subprocess.CompletedProcess, argv):
        self.proc = proc
        self.argv = argv
        self.returncode = proc.returncode
        self.stdout = proc.stdout or ""
        self.stderr = proc.stderr or ""

    @property
    def payload(self):
        """返回 stdout 中最后一个可解析的 JSON 对象；没有则返回 None。"""
        parsed = []
        for blob in iter_json_objects(self.stdout):
            try:
                value = json.loads(blob)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                parsed.append(value)
        return parsed[-1] if parsed else None

    def describe(self) -> str:
        return (
            f"\n命令: {' '.join(str(part) for part in self.argv)}"
            f"\n退出码: {self.returncode}"
            f"\nstdout:\n{self.stdout.strip()[:4000]}"
            f"\nstderr:\n{self.stderr.strip()[:4000]}"
        )


@unittest.skipUnless(READY, SKIP_REASON)
class XiezhiCliTest(unittest.TestCase):
    """基于临时 run 目录的 CLI 行为测试。"""

    def setUp(self):
        self._tmp_handle = tempfile.TemporaryDirectory(prefix="xiezhi-test-")
        self.tmp = Path(self._tmp_handle.name)
        self.root = self.tmp / "runs"
        self.source = self.tmp / "source.md"
        self.source.write_text("# 需求\n\n来源正文\n", encoding="utf-8")
        self.model_map = json.loads(MODEL_MAP_PATH.read_text(encoding="utf-8"))

    def tearDown(self):
        self._tmp_handle.cleanup()

    # ------------------------------------------------------------------ helpers
    def run_cli(self, *args, cwd=None, script=None):
        script_path = Path(script or SCRIPT)
        argv = [sys.executable, str(script_path), *[str(arg) for arg in args]]
        env = dict(os.environ)
        env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "NO_COLOR": "1",
            }
        )
        proc = subprocess.run(
            argv,
            cwd=str(cwd or PROJECT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            env=env,
        )
        return CliResult(proc, argv)

    def expect_ok(self, result: CliResult, action: str):
        self.assertEqual(result.returncode, 0, f"{action} 应成功{result.describe()}")
        payload = result.payload
        self.assertIsNotNone(payload, f"{action} 应输出 JSON{result.describe()}")
        self.assertTrue(
            payload.get("ok") is True
            or str(payload.get("status", "")).lower() in {"ok", "pass", "passed"}
            or str(payload.get("result", "")).lower() in {"ok", "pass", "passed"},
            f"{action} 的 JSON 应表示成功，实际为 {payload}{result.describe()}",
        )
        return payload

    def expect_fail(self, result: CliResult, action: str):
        self.assertNotEqual(
            result.returncode, 0, f"{action} 应以非 0 退出码拒绝{result.describe()}"
        )
        payload = result.payload
        if payload is not None:
            self.assertFalse(
                payload.get("ok") is True,
                f"{action} 拒绝时不应同时返回 ok=true{result.describe()}",
            )
        return payload

    def read_json(self, path: Path) -> dict:
        self.assertTrue(Path(path).is_file(), f"期望存在 JSON 文件: {path}")
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def pick(self, data: dict, keys, label: str, context: str = ""):
        for key in keys:
            value = data.get(key)
            if value not in (None, ""):
                return value
        self.fail(f"JSON 缺少字段 {label}（可接受键: {keys}）: {data} {context}")

    def run_dir(self, run_id: str) -> Path:
        return self.root / str(run_id)

    def start_run(self, goal: str = "整理来源并输出结论", mode: str = "standard", sources=()):
        args = ["start", "--goal", goal, "--mode", mode, "--root", str(self.root)]
        for item in sources:
            args += ["--source", str(item)]
        result = self.run_cli(*args)
        payload = self.expect_ok(result, "start")
        run_id = self.pick(payload, RUN_ID_KEYS, "run-id", result.describe())
        self.assertTrue(
            self.run_dir(run_id).is_dir(),
            f"start 应创建 run 目录 {self.run_dir(run_id)}{result.describe()}",
        )
        return run_id, payload

    def make_ticket(self, run_id, stage, role, sources=(), criteria="判据：答案需可核对"):
        args = [
            "ticket",
            "--run-id",
            run_id,
            "--stage",
            stage,
            "--role",
            role,
            "--criteria",
            criteria,
            "--root",
            str(self.root),
        ]
        for item in sources:
            args += ["--source", str(item)]
        result = self.run_cli(*args)
        payload = self.expect_ok(result, f"ticket({stage})")
        # 优先使用输出里的路径；缺失时回退到 tickets/ 下最新的 JSON。
        for key in ("ticketPath", "ticket_path", "path", "ticket"):
            value = payload.get(key)
            if value:
                candidate = Path(value)
                if not candidate.is_absolute():
                    candidate = self.run_dir(run_id) / candidate
                if candidate.is_file():
                    return candidate
        tickets_dir = self.run_dir(run_id) / "tickets"
        self.assertTrue(tickets_dir.is_dir(), f"ticket 应创建目录 {tickets_dir}{result.describe()}")
        candidates = sorted(tickets_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        self.assertTrue(candidates, f"tickets/ 下未找到票据文件{result.describe()}")
        return candidates[-1]

    def make_receipt(self, run_id, ticket_path, status="passed", actual_model=None,
                     artifacts=(), raw_output=None):
        args = [
            "receipt",
            "--run-id",
            run_id,
            "--ticket",
            str(ticket_path),
            "--status",
            status,
            "--root",
            str(self.root),
        ]
        if actual_model is not None:
            args += ["--actual-model", actual_model]
        for item in artifacts:
            args += ["--artifact", str(item)]
        if raw_output is not None:
            args += ["--raw-output", str(raw_output)]
        result = self.run_cli(*args)
        payload = self.expect_ok(result, f"receipt({Path(ticket_path).stem})")
        return result, payload

    def receipt_path(self, run_id, ticket_path) -> Path:
        ticket = self.read_json(ticket_path)
        ticket_id = self.pick(ticket, TICKET_ID_KEYS, "ticketId", f"来自 {ticket_path}")
        return self.run_dir(run_id) / "receipts" / f"{ticket_id}.json"

    def advance(self, run_id, stage):
        result = self.run_cli("advance", "--run-id", run_id, "--stage", stage, "--root", str(self.root))
        return result, result.payload

    def pass_stage(self, run_id, stage, artifact=True):
        """开票 -> 用票据请求的 modelId 作为实际模型回执 -> 推进阶段。"""
        ticket = self.make_ticket(run_id, stage, STAGE_ROLE[stage], sources=[self.source])
        ticket_data = self.read_json(ticket)
        requested = self.pick(ticket_data, ("requestedModelId", "modelId"), "requestedModelId")

        raw_output = None
        artifacts = []
        if artifact:
            artifacts.append(self.write_artifact(stage))
            raw_output = artifacts[0]

        self.make_receipt(
            run_id,
            ticket,
            status="passed",
            actual_model=requested,
            artifacts=artifacts,
            raw_output=raw_output,
        )
        result, payload = self.advance(run_id, stage)
        self.expect_ok(result, f"advance({stage})")
        return ticket, payload

    def write_artifact(self, name: str) -> Path:
        target = self.tmp / "artifacts" / f"{name}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {name} 产物\n\n阶段 {name} 已完成。\n", encoding="utf-8")
        return target

    def build_complete_run(self, mode="standard", goal="完整链路验收：整理来源并给出结论"):
        run_id, _ = self.start_run(goal=goal, mode=mode, sources=[self.source])
        for stage in self.model_map["modes"][mode]["stages"]:
            self.pass_stage(run_id, stage)
        return run_id

    def copy_config(self, name: str) -> Path:
        """只复制配置目录到临时目录，返回副本路径。"""
        target = self.tmp / name
        shutil.copytree(CONFIG_DIR, target)
        return target

    def write_model_map(self, config_dir: Path, mutate) -> None:
        path = config_dir / "model-map.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        mutate(data)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # -------------------------------------------------------------------- tests
    def test_01_preflight_passes(self):
        """preflight 在干净配置下应判定通过。"""
        result = self.run_cli("preflight", "--root", str(self.root))
        payload = self.expect_ok(result, "preflight")
        self.assertIs(payload.get("ok"), True, f"preflight 应返回 ok=true{result.describe()}")

    def test_02_preflight_rejects_custom_local_and_external_api(self):
        """注入 custom-local 模型或外部 API 字段后，preflight 必须拒绝。"""
        config_a = self.copy_config("config-custom-local")

        def inject_custom_local(data):
            data["roles"]["fast_executor"]["modelId"] = "custom-local:local-router"

        self.write_model_map(config_a, inject_custom_local)
        result = self.run_cli(
            "preflight", "--config", str(config_a), "--root", str(self.tmp / "runs-a")
        )
        self.expect_fail(result, "preflight(custom-local)")
        combined = (result.stdout + result.stderr).lower()
        self.assertTrue(
            "custom-local" in combined or "custom_local" in combined,
            f"拒绝信息应指明 custom-local 违规{result.describe()}",
        )

        config_b = self.copy_config("config-external-api")

        def inject_external_api(data):
            data["apiKey"] = "dummy-not-a-real-key"
            data["baseUrl"] = "https://example.invalid/v1"
            data["roles"]["requirements_analyst"]["endpoint"] = "https://example.invalid/v1/chat"

        self.write_model_map(config_b, inject_external_api)
        result2 = self.run_cli(
            "preflight", "--config", str(config_b), "--root", str(self.tmp / "runs-b")
        )
        self.expect_fail(result2, "preflight(external-api)")
        parsed = result2.payload
        if parsed is not None:
            self.assertIsNot(parsed.get("ok"), True, f"外部 API 配置不得通过{result2.describe()}")

    def test_03_start_is_idempotent(self):
        """相同 goal + 相同来源哈希应复用 run-id；goal 不同则新建。"""
        run_id_first, first = self.start_run(mode="standard", sources=[self.source])
        run_id_second, second = self.start_run(mode="standard", sources=[self.source])
        self.assertEqual(
            run_id_first,
            run_id_second,
            f"相同 goal/source 应返回同一 run-id{json.dumps([first, second], ensure_ascii=False)}",
        )

        spec = self.read_json(self.run_dir(run_id_first) / "task-spec.json")
        self.assertEqual(spec.get("mode"), "standard", f"task-spec.mode 应为 standard: {spec}")
        sources = spec.get("sources")
        self.assertIsInstance(sources, list, f"task-spec.sources 应为列表: {spec}")
        self.assertTrue(sources, f"task-spec.sources 不应为空: {spec}")
        entry = sources[0]
        self.assertEqual(
            entry.get("sha256"),
            sha256_file(self.source),
            f"task-spec 中来源哈希应与文件一致: {entry}",
        )
        self.assertIn("idempotencyKey", spec, f"task-spec 应记录 idempotencyKey: {spec}")

        run_id_other, _ = self.start_run(goal="另一个完全不同的目标", mode="standard", sources=[self.source])
        self.assertNotEqual(run_id_other, run_id_first, "不同 goal 应生成不同 run-id")

    def test_04_ticket_binds_model_id_and_source_hash(self):
        """票据应绑定 model-map 中的 agentName/modelId/family 与来源 SHA256。"""
        run_id, _ = self.start_run(mode="standard", sources=[self.source])
        stage = "coordinate"
        role_key = STAGE_ROLE[stage]
        expected = self.model_map["roles"][role_key]

        ticket_path = self.make_ticket(run_id, stage, role_key, sources=[self.source])
        ticket = self.read_json(ticket_path)

        self.assertEqual(ticket.get("stage"), stage, f"票据 stage 不符: {ticket}")
        self.assertEqual(ticket.get("role"), role_key, f"票据 role 不符: {ticket}")
        self.assertEqual(
            ticket.get("requestedModelId"),
            expected["modelId"],
            f"票据 requestedModelId 应来自 model-map: 期望 {expected['modelId']}, 实际 {ticket}",
        )
        self.assertEqual(ticket.get("agentName"), expected["agentName"], f"agentName 不符: {ticket}")
        self.assertEqual(ticket.get("family"), expected["family"], f"family 不符: {ticket}")
        self.assertEqual(
            list(ticket.get("allowedTools") or []),
            list(expected["tools"]),
            f"allowedTools 应沿用 model-map 定义: {ticket}",
        )
        self.assertIn("ticketId", ticket, f"票据应含 ticketId: {ticket}")
        self.assertTrue(
            str(ticket_path).startswith(str(self.run_dir(run_id) / "tickets")),
            f"票据应写在 run 的 tickets/ 下: {ticket_path}",
        )

        refs = ticket.get("sourceRefs")
        self.assertIsInstance(refs, list, f"票据应含 sourceRefs 列表: {ticket}")
        self.assertTrue(refs, f"sourceRefs 不应为空: {ticket}")
        hashes = {ref.get("sha256") for ref in refs if isinstance(ref, dict)}
        self.assertIn(
            sha256_file(self.source),
            hashes,
            f"sourceRefs 应包含来源文件真实 SHA256: {refs}",
        )

        ledger = self.read_json(self.run_dir(run_id) / "run-ledger.json")
        self.assertIn("stages", ledger, f"run-ledger 应含 stages: {ledger}")

    def test_05_passed_receipt_without_actual_model_becomes_unknown(self):
        """缺 actual-model 的 passed 回执应降级为 unknown，且不得推进阶段。"""
        run_id, _ = self.start_run(mode="standard", sources=[self.source])
        stage = "coordinate"
        ticket = self.make_ticket(run_id, stage, STAGE_ROLE[stage], sources=[self.source])

        result, payload = self.make_receipt(run_id, ticket, status="passed")
        receipt_path = self.receipt_path(run_id, ticket)
        receipt = self.read_json(receipt_path)

        self.assertEqual(
            str(receipt.get("status", "")).lower(),
            "unknown",
            f"无 actualModel 的 passed 回执必须降级为 unknown: {receipt}",
        )
        reported = str(payload.get("status", "")).lower() if payload else ""
        if reported:
            self.assertEqual(reported, "unknown", f"命令输出也应报告降级后状态{result.describe()}")

        advance_result, _ = self.advance(run_id, stage)
        self.expect_fail(advance_result, f"advance({stage}) 在 unknown 回执后")

        ledger = self.read_json(self.run_dir(run_id) / "run-ledger.json")
        self.assertNotEqual(
            self.stage_status(ledger, stage),
            "passed",
            f"unknown 回执不得把阶段标为 passed: {ledger}",
        )

    def test_06_advance_rejects_changed_source(self):
        """来源文件在开票后被修改，advance 应因哈希不一致拒绝。"""
        run_id, _ = self.start_run(mode="standard", sources=[self.source])
        stage = "coordinate"
        ticket = self.make_ticket(run_id, stage, STAGE_ROLE[stage], sources=[self.source])
        ticket_data = self.read_json(ticket)
        requested = self.pick(ticket_data, ("requestedModelId", "modelId"), "requestedModelId")
        self.make_receipt(
            run_id,
            ticket,
            status="passed",
            actual_model=requested,
            artifacts=[self.write_artifact(stage)],
        )

        self.source.write_text("# 需求\n\n来源正文已被改写，哈希变化\n", encoding="utf-8")

        result, _ = self.advance(run_id, stage)
        self.expect_fail(result, f"advance({stage}) 在来源变更后")
        ledger = self.read_json(self.run_dir(run_id) / "run-ledger.json")
        self.assertNotEqual(
            self.stage_status(ledger, stage),
            "passed",
            f"来源变更后阶段不得 passed: {ledger}",
        )

    def test_07_reviewer_excludes_author_family(self):
        """复核人应跳过与作者同家族的角色，按 candidateRoleOrder 选下一个。"""
        run_id, _ = self.start_run(mode="strict", sources=[self.source])
        stage = "execute"
        ticket = self.make_ticket(run_id, stage, "fast_executor", sources=[self.source])
        ticket_data = self.read_json(ticket)
        author_family = ticket_data.get("family") or self.model_map["roles"]["fast_executor"]["family"]
        requested = self.pick(ticket_data, ("requestedModelId", "modelId"), "requestedModelId")
        self.make_receipt(
            run_id,
            ticket,
            status="passed",
            actual_model=requested,
            artifacts=[self.write_artifact("execute")],
        )

        author_families = {author_family}
        expected_role = None
        for role_key in self.model_map["reviewPolicy"]["candidateRoleOrder"]:
            role = self.model_map["roles"].get(role_key)
            if role and role["family"] not in author_families:
                expected_role = role_key
                break

        result = self.run_cli("reviewer", "--run-id", run_id, "--root", str(self.root))
        payload = self.expect_ok(result, "reviewer")
        recommended_role = payload.get("recommendedRole")
        recommended_model = payload.get("recommendedModelId")
        independence = payload.get("independenceLevel")

        if expected_role is None:
            self.assertEqual(
                recommended_role,
                "context_review",
                f"无跨家族候选时应降级 context_review: {payload}",
            )
        else:
            self.assertEqual(
                recommended_role,
                expected_role,
                f"应按 candidateRoleOrder 选出首个非同家族角色 {expected_role}: {payload}",
            )
            self.assertEqual(
                recommended_model,
                self.model_map["roles"][expected_role]["modelId"],
                f"recommendedModelId 应与角色定义一致: {payload}",
            )
            recommended_family = self.model_map["roles"][expected_role]["family"]
            self.assertNotIn(
                recommended_family,
                author_families,
                f"复核人不得与作者同家族（作者 {author_families}，推荐 {recommended_family}）",
            )
            self.assertIsNotNone(independence, f"应输出 independenceLevel: {payload}")

    def test_08_revise_beyond_mode_limit_is_rejected(self):
        """返修轮次超过模式上限时应拒绝退出，并且保留轮次记录。"""
        mode = "standard"
        limit = int(self.model_map["modes"][mode]["maxRevisions"])
        run_id, _ = self.start_run(mode=mode, sources=[self.source])
        for stage in ("coordinate", "analyze", "route", "execute"):
            self.pass_stage(run_id, stage)

        for index in range(limit):
            result = self.run_cli(
                "revise", "--run-id", run_id, "--reason", f"第 {index + 1} 轮返修", "--root", str(self.root)
            )
            self.expect_ok(result, f"revise 第 {index + 1} 轮（上限 {limit}）")

        over = self.run_cli(
            "revise", "--run-id", run_id, "--reason", "超过上限的一轮", "--root", str(self.root)
        )
        self.expect_fail(over, f"revise 第 {limit + 1} 轮（超出上限 {limit}）")

        revisions_dir = self.run_dir(run_id) / "revisions"
        self.assertTrue(revisions_dir.is_dir(), f"应存在 revisions 目录: {revisions_dir}")
        rounds = sorted(revisions_dir.glob("round-*.json"))
        if limit > 0:
            self.assertEqual(
                len(rounds),
                limit,
                f"返修记录数量应与允许轮次一致（上限 {limit}）: {[p.name for p in rounds]}",
            )
            record = self.read_json(rounds[0])
            self.assertTrue(
                "reason" in record or "idempotencyKey" in record,
                f"返修记录应含原因与幂等键: {record}",
            )

    def test_09_report_generates_html_with_run_id_and_stages(self):
        """report 应生成单文件 HTML，含 run-id、目标与阶段关键字。"""
        goal = "生成报告：整理来源并输出结论"
        run_id = self.build_complete_run(mode="standard", goal=goal)

        result = self.run_cli("report", "--run-id", run_id, "--root", str(self.root))
        self.expect_ok(result, "report")

        report_path = self.run_dir(run_id) / "report.html"
        self.assertTrue(report_path.is_file(), f"应生成 report.html: {report_path}{result.describe()}")
        html = report_path.read_text(encoding="utf-8")
        self.assertIn("<html", html.lower(), "报告应是 HTML 文档")
        self.assertIn(str(run_id), html, "报告应包含 run-id")
        self.assertIn(goal[:12], html, "报告应包含目标文本")
        for stage in self.model_map["modes"]["standard"]["stages"]:
            self.assertIn(stage, html.lower(), f"报告应包含阶段关键字 {stage}")
        self.assertNotIn("<script src=", html.lower(), "报告不得引入外部 JS")
        self.assertNotIn('rel="stylesheet"', html.lower(), "报告不得引入外部 CSS")

    def test_10_verify_passes_on_complete_run(self):
        """完整链路（八阶段全部 passed）应通过 verify。"""
        run_id = self.build_complete_run(mode="standard")
        result = self.run_cli("verify", "--run-id", run_id, "--root", str(self.root))
        self.expect_ok(result, "verify(完整链路)")

    def test_11_verify_detects_missing_receipt(self):
        """删除某阶段回执后，verify 必须报告失败（故障路径）。"""
        run_id = self.build_complete_run(mode="standard")
        stage = "execute"
        receipts_dir = self.run_dir(run_id) / "receipts"
        targets = sorted(receipts_dir.glob("*.json"))
        self.assertTrue(targets, f"完整链路应留下回执: {receipts_dir}")
        victims = [path for path in targets if stage in path.name] or targets[:1]
        for path in victims:
            path.unlink()

        result = self.run_cli("verify", "--run-id", run_id, "--root", str(self.root))
        self.expect_fail(result, f"verify 在删除 {stage} 回执后")

    # ------------------------------------------------------------------ helpers2
    @staticmethod
    def stage_status(ledger: dict, stage: str) -> str:
        stages = ledger.get("stages") or {}
        value = stages.get(stage)
        if isinstance(value, dict):
            return str(value.get("status", "")).lower()
        if isinstance(value, str):
            return value.lower()
        return ""


if __name__ == "__main__":
    unittest.main(verbosity=2)
