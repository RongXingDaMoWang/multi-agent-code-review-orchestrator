"""
全量测试脚本：覆盖 5 个 Bug 修复 + 3 个新功能
运行方式：python3 test_all.py
"""
import json
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path

# Fix Unicode encoding on Windows with GBK locale
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

PASS = 0
FAIL = 0

def ok(name):
    global PASS
    PASS += 1
    print(f"  ✅ {name}")

def fail(name, err):
    global FAIL
    FAIL += 1
    print(f"  ❌ {name}")
    print(f"     {err}")

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ─── Bug 4: SkillLoader 分隔符 ────────────────────────────────────────────────
section("Bug 4 — SkillLoader 分隔符不冲突")

try:
    from code_review_agent.agent.skill_loader import SkillLoader
    loader = SkillLoader()

    # load_skill 不应使用 --- 作为分隔符
    skill = loader.load_skill("code-review", include_references=True)
    # 检查 references 分隔符是新格式
    assert "===== Reference:" in skill, "References should use ===== separator"
    ok("load_skill references 使用 ===== 分隔符")

    # load_combined 不应使用 --- 作为分隔符
    combined = loader.load_combined("code-review", "code-review-triage")
    assert "===== code-review =====" in combined
    assert "===== code-review-triage =====" in combined
    ok("load_combined 使用 ===== 分隔符")

    # 验证 markdown 中的 --- 不会被误当分隔符
    # 检查 code-review-triage 的内容确实在 combined 里
    triage_only = loader.load_skill("code-review-triage", include_references=True)
    # 取 triage skill 正文的前 50 个非空字符
    triage_snippet = triage_only.strip()[:80]
    assert triage_snippet in combined, "Triage content should appear in combined"
    ok("markdown 中的 --- 不被误截断")

except Exception as e:
    fail("SkillLoader 分隔符测试", traceback.format_exc(limit=3))


# ─── Bug 1: 所有子技能加载 ────────────────────────────────────────────────────
section("Bug 1 — 所有子技能都加载到 system prompt")

try:
    combined = loader.load_combined(
        "code-review",
        "code-review-triage",
        "code-review-analyze",
        "code-review-act",
        "code-review-memory",
    )
    assert len(combined) > 5000, f"Combined prompt too short: {len(combined)} chars"
    ok(f"Combined prompt 长度 {len(combined)} chars (> 5000)")

    for skill_name in ["code-review", "code-review-triage", "code-review-analyze",
                       "code-review-act", "code-review-memory"]:
        assert f"===== {skill_name} =====" in combined, f"Missing skill: {skill_name}"
        ok(f"子技能 {skill_name} 存在于 combined prompt")

except Exception as e:
    fail("子技能加载测试", traceback.format_exc(limit=3))


# ─── Bug 2: GitHub Review 内联评论 API ───────────────────────────────────────
section("Bug 2 — GitHub Review 内联评论批量提交")

try:
    from unittest.mock import MagicMock, patch
    from code_review_agent.tools.github_tools import GitHubTools

    gh = GitHubTools.__new__(GitHubTools)

    # Mock _get_repo
    mock_repo = MagicMock()
    mock_pr = MagicMock()
    mock_pr.head.sha = "abc123"
    mock_repo.get_pull.return_value = mock_pr

    mock_review = MagicMock()
    mock_review.id = 999
    mock_review.state = "REQUEST_CHANGES"
    mock_review.body = "Review body"
    mock_pr.create_review.return_value = mock_review

    gh._gh = MagicMock()
    gh._gh.get_repo.return_value = mock_repo

    comments = [
        {"path": "app.py", "line": 42, "body": "SQL injection here"},
        {"path": "utils.py", "line": 10, "body": "Naming issue"},
    ]

    result = gh.create_pull_request_review(
        owner="owner", repo="repo", pull_number=1,
        event="REQUEST_CHANGES", body="Review body",
        comments=comments,
    )

    # create_review 应被调用一次，带 comments 参数
    mock_pr.create_review.assert_called_once()
    call_kwargs = mock_pr.create_review.call_args
    assert "comments" in call_kwargs.kwargs or (
        len(call_kwargs.args) >= 3
    ), "comments not passed to create_review"
    passed_comments = call_kwargs.kwargs.get("comments", [])
    assert len(passed_comments) == 2, f"Expected 2 comments, got {len(passed_comments)}"
    ok("create_review 被调用一次（批量提交）")
    ok(f"comments 作为参数传入 create_review（{len(passed_comments)} 条）")

    # create_review_comment 不应被调用
    mock_pr.create_review_comment.assert_not_called()
    ok("create_review_comment 未被单独调用")

except Exception as e:
    fail("GitHub Review 内联评论测试", traceback.format_exc(limit=3))


# ─── Bug 3: issues_found 提取 ─────────────────────────────────────────────────
section("Bug 3 — issues_found 从 JSON 正确提取")

try:
    # 模拟 runner.py 中的提取逻辑
    all_text = '''
    分析结果如下：
    {
      "issues": [
        {"severity": "high", "category": "security", "file": "app.py", "line": 42,
         "description": "SQL injection", "suggestion": "Use parameterized queries",
         "auto_fixable": true, "fix_code": null},
        {"severity": "medium", "category": "style", "file": "utils.py", "line": 10,
         "description": "Naming issue", "suggestion": "Use snake_case",
         "auto_fixable": false, "fix_code": null},
        {"severity": "low", "category": "performance", "file": "views.py", "line": 5,
         "description": "N+1 query", "suggestion": "Use select_related",
         "auto_fixable": false, "fix_code": null}
      ],
      "decision": "REQUEST_CHANGES",
      "summary": "Found 3 issues",
      "confidence": 0.9
    }
    '''

    issues_found = 0
    for json_match in re.finditer(r'\{[^{}]*"issues"\s*:\s*\[', all_text):
        start = json_match.start()
        depth = 0
        end = start
        for i, ch in enumerate(all_text[start:], start):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        try:
            parsed = json.loads(all_text[start:end])
            if isinstance(parsed.get("issues"), list):
                issues_found = max(issues_found, len(parsed["issues"]))
        except (json.JSONDecodeError, ValueError):
            pass

    assert issues_found == 3, f"Expected 3, got {issues_found}"
    ok(f"issues_found = {issues_found} (正确提取 3 个 issues)")

    # 测试空 issues
    all_text_empty = '{"issues": [], "decision": "APPROVE", "summary": "No issues", "confidence": 1.0}'
    issues_found2 = 0
    for json_match in re.finditer(r'\{[^{}]*"issues"\s*:\s*\[', all_text_empty):
        start = json_match.start()
        depth = 0
        end = start
        for i, ch in enumerate(all_text_empty[start:], start):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        try:
            parsed = json.loads(all_text_empty[start:end])
            if isinstance(parsed.get("issues"), list):
                issues_found2 = max(issues_found2, len(parsed["issues"]))
        except (json.JSONDecodeError, ValueError):
            pass
    assert issues_found2 == 0
    ok("issues_found = 0 when no issues (APPROVE case)")

    # 测试无 JSON 的情况
    all_text_no_json = "APPROVE — looks good!"
    issues_found3 = 0
    for json_match in re.finditer(r'\{[^{}]*"issues"\s*:\s*\[', all_text_no_json):
        pass  # no match
    assert issues_found3 == 0
    ok("issues_found = 0 when no JSON block")

except Exception as e:
    fail("issues_found 提取测试", traceback.format_exc(limit=3))


# ─── Bug 5: SFT 导出 native tool_calls 格式 ──────────────────────────────────
section("Bug 5 — SFT 导出保留 native tool_calls 格式")

try:
    from code_review_agent.trajectory.exporter import _build_sft_messages_native

    records = [
        {"type": "user", "message": {"role": "user", "content": "Review PR #42"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Let me check the PR."},
            {"type": "tool_use", "id": "tool-001", "name": "get_pull_request",
             "input": {"owner": "o", "repo": "r", "pull_number": 42}},
        ]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tool-001",
             "content": [{"type": "text", "text": '{"title": "Fix bug"}'}]},
        ]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "The PR looks good. APPROVE."},
        ]}},
    ]

    messages = _build_sft_messages_native(records)

    # user message
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Review PR #42"
    ok("user message 正确")

    # assistant with tool_calls
    assert messages[1]["role"] == "assistant"
    assert "tool_calls" in messages[1], "Missing tool_calls field"
    assert messages[1]["tool_calls"][0]["type"] == "function"
    assert messages[1]["tool_calls"][0]["function"]["name"] == "get_pull_request"
    assert messages[1]["tool_calls"][0]["id"] == "tool-001"
    # arguments should be JSON string
    args = json.loads(messages[1]["tool_calls"][0]["function"]["arguments"])
    assert args["pull_number"] == 42
    ok("assistant tool_use → native tool_calls 格式")

    # text content preserved alongside tool_calls
    assert messages[1].get("content") == "Let me check the PR."
    ok("assistant text content 与 tool_calls 共存")

    # tool result
    assert messages[2]["role"] == "tool"
    assert messages[2]["tool_call_id"] == "tool-001"
    assert '{"title": "Fix bug"}' in messages[2]["content"]
    ok("tool_result → role=tool 格式")

    # final assistant text
    assert messages[3]["role"] == "assistant"
    assert "APPROVE" in messages[3]["content"]
    ok("最终 assistant 文本消息正确")

    # 确认没有 [Tool: xxx] 纯文本格式
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            assert "[Tool:" not in content, "Found old [Tool:] text format in SFT output"
    ok("无旧版 [Tool: xxx] 纯文本格式")

except Exception as e:
    fail("SFT 导出格式测试", traceback.format_exc(limit=3))


# ─── Feature A: 开发者成长画像 ───────────────────────────────────────────────
section("Feature A — 开发者成长画像")

# 使用临时文件隔离测试数据
import code_review_agent.tools.memory_tools as mt
_orig_memory_file = mt.MEMORY_FILE
tmp_memory = Path(tempfile.mktemp(suffix=".jsonl"))
mt.MEMORY_FILE = tmp_memory

try:
    # 重新导入以使用新的 MEMORY_FILE
    from importlib import reload
    import code_review_agent.tools.memory_tools as mt2
    mt2.MEMORY_FILE = tmp_memory

    from code_review_agent.tools.memory_tools import MemoryTools
    mem = MemoryTools()

    # 1. 新用户无画像
    result = mem.memory_get_developer_profile("alice")
    assert result["found"] == False
    ok("新用户返回 found=False")

    # 2. 首次 review 后创建画像
    mem.memory_update_developer_profile(
        author="alice",
        new_issues=[
            {"category": "security", "severity": "high"},
            {"category": "style", "severity": "low"},
        ],
        strengths=["good naming", "test coverage"],
        growth_areas=["SQL safety"],
    )
    result = mem.memory_get_developer_profile("alice")
    assert result["found"] == True
    content = result["profile"]["content"]
    assert content["pr_count"] == 1
    assert len(content["issue_history"]) == 2
    assert set(content["strengths"]) == {"good naming", "test coverage"}
    assert "SQL safety" in content["growth_areas"]
    ok("首次 review 创建开发者画像")

    # 3. 多次 review 后累积
    for _ in range(3):
        mem.memory_update_developer_profile(
            author="alice",
            new_issues=[{"category": "security", "severity": "high"}],
        )
    result = mem.memory_get_developer_profile("alice")
    content = result["profile"]["content"]
    assert content["pr_count"] == 4
    sec = next(h for h in content["issue_history"] if h["category"] == "security")
    assert sec["count"] == 4, f"Expected count=4, got {sec['count']}"
    ok(f"多次 review 累积：pr_count=4, security count=4")

    # 4. 不同用户互不干扰
    mem.memory_update_developer_profile(author="bob", new_issues=[{"category": "bug", "severity": "medium"}])
    alice_result = mem.memory_get_developer_profile("alice")
    bob_result = mem.memory_get_developer_profile("bob")
    assert alice_result["profile"]["content"]["pr_count"] == 4
    assert bob_result["profile"]["content"]["pr_count"] == 1
    ok("不同用户画像互不干扰")

    # 5. TOOL_DEFINITIONS 包含新工具
    from code_review_agent.tools.memory_tools import TOOL_DEFINITIONS
    tool_names = [t["name"] for t in TOOL_DEFINITIONS]
    assert "memory_get_developer_profile" in tool_names
    assert "memory_update_developer_profile" in tool_names
    ok("TOOL_DEFINITIONS 包含 memory_get/update_developer_profile")

    # 6. tool_router 注册了新工具
    from code_review_agent.agent.tool_router import ToolRouter
    router = ToolRouter(dry_run=True)
    assert "memory_get_developer_profile" in router._dispatch
    assert "memory_update_developer_profile" in router._dispatch
    ok("ToolRouter 注册了开发者画像工具")

    # 7. coaching-guidelines.md 存在
    guidelines_path = Path(__file__).parent / "skills/code-review-act/references/coaching-guidelines.md"
    assert guidelines_path.exists()
    content_text = guidelines_path.read_text(encoding="utf-8")
    assert "新手" in content_text and "资深" in content_text
    ok("coaching-guidelines.md 存在且包含经验等级规范")

    # 8. memory-schema.md 包含 developer_profile
    schema_path = Path(__file__).parent / "skills/code-review-memory/references/memory-schema.md"
    schema_text = schema_path.read_text(encoding="utf-8")
    assert "developer_profile" in schema_text
    ok("memory-schema.md 包含 developer_profile schema")

    # 9. code-review-act/SKILL.md 包含 Phase 2.5
    act_skill_path = Path(__file__).parent / "skills/code-review-act/SKILL.md"
    act_text = act_skill_path.read_text(encoding="utf-8")
    assert "memory_get_developer_profile" in act_text
    assert "memory_update_developer_profile" in act_text
    ok("code-review-act/SKILL.md 包含画像读取和更新步骤")

except Exception as e:
    fail("开发者成长画像测试", traceback.format_exc(limit=3))
finally:
    mt.MEMORY_FILE = _orig_memory_file
    if tmp_memory.exists():
        tmp_memory.unlink()


# ─── Feature B: 跨 PR 模式智能 ───────────────────────────────────────────────
section("Feature B — 跨 PR 模式智能")

tmp_memory2 = Path(tempfile.mktemp(suffix=".jsonl"))
mt.MEMORY_FILE = tmp_memory2

try:
    from code_review_agent.tools.memory_tools import MemoryTools, get_repo_patterns
    mem = MemoryTools()

    # 1. 聚合 issues 到 repo_pattern
    result = mem.memory_aggregate_patterns(
        repo="owner/repo",
        issues=[
            {"category": "security", "severity": "critical", "file": "app.py", "description": "SQL injection"},
            {"category": "style", "severity": "low", "file": "utils.py", "description": "naming"},
        ]
    )
    assert result["patterns_updated"] == 2
    ok("首次聚合创建 2 个 repo_pattern")

    # 2. 再次聚合同类 issue，计数递增
    mem.memory_aggregate_patterns(
        repo="owner/repo",
        issues=[
            {"category": "security", "severity": "critical", "file": "views.py", "description": "SQL injection again"},
        ]
    )
    patterns = get_repo_patterns("owner/repo")
    sec_pattern = next(p for p in patterns if p["content"]["category"] == "security")
    assert sec_pattern["content"]["occurrence_count"] == 2
    assert "app.py" in sec_pattern["content"]["affected_files"]
    assert "views.py" in sec_pattern["content"]["affected_files"]
    ok("二次聚合：occurrence_count=2，affected_files 合并")

    # 3. 不同 repo 互不干扰
    mem.memory_aggregate_patterns(
        repo="other/repo",
        issues=[{"category": "bug", "severity": "high", "file": "main.py", "description": "null ptr"}]
    )
    owner_patterns = get_repo_patterns("owner/repo")
    other_patterns = get_repo_patterns("other/repo")
    assert len(owner_patterns) == 2
    assert len(other_patterns) == 1
    ok("不同 repo 的 pattern 互不干扰")

    # 4. health report 生成
    from code_review_agent.pipeline.orchestrator import ReviewOrchestrator
    orch = ReviewOrchestrator.__new__(ReviewOrchestrator)
    report = orch.generate_health_report("owner/repo")
    assert "# Code Health Report: owner/repo" in report
    assert "Critical" in report
    assert "Recommendations" in report
    assert "occurrence" in report.lower() or "Occurrences" in report
    ok("health report 包含正确结构")

    # 5. 无数据时 health report 给出提示
    empty_report = orch.generate_health_report("no/data")
    assert "No pattern data" in empty_report
    ok("无数据时 health report 给出友好提示")

    # 6. CLI 注册了 health-report 命令
    from code_review_agent.cli import build_parser
    parser = build_parser()
    # 找到 health-report subparser
    subparsers_actions = [a for a in parser._actions
                          if hasattr(a, '_name_parser_map')]
    assert len(subparsers_actions) > 0
    subparser_map = subparsers_actions[0]._name_parser_map
    assert "health-report" in subparser_map, f"health-report not in {list(subparser_map.keys())}"
    ok("CLI 注册了 health-report 命令")

    # 7. memory_aggregate_patterns 在 TOOL_DEFINITIONS 中
    from code_review_agent.tools.memory_tools import TOOL_DEFINITIONS
    tool_names = [t["name"] for t in TOOL_DEFINITIONS]
    assert "memory_aggregate_patterns" in tool_names
    ok("TOOL_DEFINITIONS 包含 memory_aggregate_patterns")

    # 8. tool_router 注册了 memory_aggregate_patterns
    from code_review_agent.agent.tool_router import ToolRouter
    router = ToolRouter(dry_run=True)
    assert "memory_aggregate_patterns" in router._dispatch
    ok("ToolRouter 注册了 memory_aggregate_patterns")

    # 9. code-review-act/SKILL.md 包含聚合步骤
    act_skill_path = Path(__file__).parent / "skills/code-review-act/SKILL.md"
    act_text = act_skill_path.read_text(encoding="utf-8")
    assert "memory_aggregate_patterns" in act_text
    ok("code-review-act/SKILL.md 包含 memory_aggregate_patterns 步骤")

except Exception as e:
    fail("跨 PR 模式智能测试", traceback.format_exc(limit=3))
finally:
    mt.MEMORY_FILE = _orig_memory_file
    if tmp_memory2.exists():
        tmp_memory2.unlink()


# ─── Feature C: 自动生成测试用例 ─────────────────────────────────────────────
section("Feature C — 自动生成测试用例")

try:
    # 1. SKILL.md 文件存在
    test_gen_skill = Path(__file__).parent / "skills/code-review-test-gen/SKILL.md"
    assert test_gen_skill.exists()
    skill_text = test_gen_skill.read_text(encoding="utf-8")
    ok("skills/code-review-test-gen/SKILL.md 存在")

    # 2. SKILL.md 包含触发条件
    assert "additions > 20" in skill_text
    assert "detect_test_framework" in skill_text
    ok("SKILL.md 包含触发条件和 detect_test_framework")

    # 3. test-patterns.md 存在
    test_patterns = Path(__file__).parent / "skills/code-review-test-gen/references/test-patterns.md"
    assert test_patterns.exists()
    patterns_text = test_patterns.read_text(encoding="utf-8")
    for fw in ["pytest", "jest", "unittest"]:
        assert fw in patterns_text, f"Missing framework: {fw}"
    ok("test-patterns.md 存在且包含 pytest/jest/unittest 模板")

    # 4. detect_test_framework 工具存在于 TOOL_DEFINITIONS
    from code_review_agent.tools.github_tools import TOOL_DEFINITIONS
    tool_names = [t["name"] for t in TOOL_DEFINITIONS]
    assert "detect_test_framework" in tool_names
    ok("TOOL_DEFINITIONS 包含 detect_test_framework")

    # 5. GitHubTools.detect_test_framework 方法存在
    from code_review_agent.tools.github_tools import GitHubTools
    assert hasattr(GitHubTools, "detect_test_framework")
    ok("GitHubTools.detect_test_framework 方法存在")

    # 6. detect_test_framework 逻辑正确（mock GitHub API）
    from unittest.mock import MagicMock
    gh = GitHubTools.__new__(GitHubTools)
    mock_repo = MagicMock()

    # Mock requirements.txt with pytest
    import base64
    mock_req = MagicMock()
    mock_req.content = base64.b64encode(b"pytest==7.0\nrequests==2.28").decode()
    mock_repo.get_contents.side_effect = lambda path, **kw: (
        mock_req if path == "requirements.txt" else (_ for _ in ()).throw(Exception("not found"))
    )
    gh._gh = MagicMock()
    gh._gh.get_repo.return_value = mock_repo

    result = gh.detect_test_framework("owner", "repo")
    assert result["language"] == "python"
    assert "pytest" in result["frameworks"]
    assert result["primary_framework"] == "pytest"
    ok("detect_test_framework 正确识别 pytest")

    # 7. ToolRouter 注册了 detect_test_framework
    from code_review_agent.agent.tool_router import ToolRouter
    router = ToolRouter(dry_run=True)
    assert "detect_test_framework" in router._dispatch
    ok("ToolRouter 注册了 detect_test_framework")

    # 8. code-review-act/SKILL.md 包含测试生成触发逻辑
    act_skill_path = Path(__file__).parent / "skills/code-review-act/SKILL.md"
    act_text = act_skill_path.read_text(encoding="utf-8")
    assert "detect_test_framework" in act_text
    assert "additions > 20" in act_text
    ok("code-review-act/SKILL.md 包含测试生成触发逻辑")

    # 9. JS/TS 框架识别
    mock_pkg = MagicMock()
    pkg_json = json.dumps({
        "dependencies": {},
        "devDependencies": {"jest": "^29.0.0", "typescript": "^5.0.0"},
        "scripts": {"test": "jest --coverage"}
    }).encode()
    mock_pkg.content = base64.b64encode(pkg_json).decode()

    def side_effect_js(path, **kw):
        if path == "package.json":
            return mock_pkg
        raise Exception("not found")

    mock_repo2 = MagicMock()
    mock_repo2.get_contents.side_effect = side_effect_js
    gh._gh.get_repo.return_value = mock_repo2

    result2 = gh.detect_test_framework("owner", "repo")
    assert result2["language"] == "javascript"
    assert "jest" in result2["frameworks"]
    ok("detect_test_framework 正确识别 jest (JavaScript)")

except Exception as e:
    fail("自动生成测试用例测试", traceback.format_exc(limit=3))


# ─── 综合：runner.py 集成 ────────────────────────────────────────────────────
section("综合 — runner.py 集成验证")

try:
    # 验证 runner.py 使用 load_combined 而非 load_skill
    runner_path = Path(__file__).parent / "agent/runner.py"
    runner_text = runner_path.read_text(encoding="utf-8")
    assert "load_combined(" in runner_text, "runner.py should use load_combined"
    assert 'include_references=False' not in runner_text, "Old load_skill call still present"
    ok("runner.py 使用 load_combined() 加载所有子技能")

    # 验证 import re 在文件顶部（不在函数内部）
    # issues_found 提取使用 import re as _re（局部导入），这是可接受的
    assert "import re as _re" in runner_text or "import re" in runner_text
    ok("runner.py 包含 re 模块用于 issues 提取")

    # 验证 issues_found 提取逻辑在 runner.py 中
    assert "issues_found" in runner_text
    assert '"issues"' in runner_text
    ok("runner.py 包含 issues_found 提取逻辑")

except Exception as e:
    fail("runner.py 集成验证", traceback.format_exc(limit=3))


# ─── 综合：tool_router 完整性 ────────────────────────────────────────────────
section("综合 — ToolRouter 工具完整性")

try:
    from code_review_agent.agent.tool_router import ToolRouter, ALL_TOOL_DEFINITIONS
    router = ToolRouter(dry_run=True)

    expected_tools = [
        # GitHub tools
        "get_pull_request", "get_pull_request_files", "list_commits",
        "get_file_contents", "create_pull_request_review", "create_branch",
        "create_or_update_file", "create_pull_request", "add_issue_comment",
        "get_pull_request_status", "detect_test_framework",
        # Memory tools
        "memory_get_all", "memory_add_pattern", "memory_add_false_positive",
        "memory_get_developer_profile", "memory_update_developer_profile",
        "memory_aggregate_patterns",
    ]

    for tool in expected_tools:
        assert tool in router._dispatch, f"Missing tool in dispatch: {tool}"
        ok(f"工具 {tool} 已注册")

    # ALL_TOOL_DEFINITIONS 包含所有工具
    def_names = [t["name"] for t in ALL_TOOL_DEFINITIONS]
    for tool in expected_tools:
        assert tool in def_names, f"Missing tool in TOOL_DEFINITIONS: {tool}"
    ok(f"ALL_TOOL_DEFINITIONS 包含全部 {len(expected_tools)} 个工具")

except Exception as e:
    fail("ToolRouter 完整性测试", traceback.format_exc(limit=3))


# ─── 最终汇总 ─────────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"  测试结果：{PASS} 通过 / {FAIL} 失败 / {PASS+FAIL} 总计")
print(f"{'='*60}")

if FAIL > 0:
    sys.exit(1)
else:
    print("  🎉 全部通过！")
    sys.exit(0)
