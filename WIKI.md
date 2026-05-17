# Code Review Agent 技术文档

> 自动化代码审查系统，基于 Claude 的 Agent 架构，支持 GitHub PR 自动审查、安全漏洞扫描、代码质量检查和自动修复。

---

## 📚 文档导航

| 章节 | 内容 | 适合人群 |
|------|------|----------|
| [快速开始](#快速开始) | 5 分钟上手教程 | 新手 |
| [核心概念](#核心概念) | Agent、Skill、Tool 等概念解释 | 新手 |
| [架构设计](#架构设计) | 系统架构和模块划分 | 进阶 |
| [详细指南](#详细指南) | 各模块技术细节 | 进阶 |
| [API 参考](#api-参考) | 工具和配置参考 | 开发者 |
| [最佳实践](#最佳实践) | 使用建议和常见问题 | 所有用户 |

---

## 快速开始

### 1. 环境准备

```bash
# 克隆项目
git clone <repository-url>
cd code_review_agent

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
# GitHub Token（必需）
export GITHUB_TOKEN="ghp_your_github_token"

# Anthropic API Key（必需）
export ANTHROPIC_API_KEY="sk-ant-api-xxx"

# 可选配置
export ANTHROPIC_MODEL="claude-opus-4-6"  # 默认模型
```

**获取 GitHub Token**：
1. 访问 https://github.com/settings/tokens
2. 点击 "Generate new token (classic)"
3. 勾选 `repo` 权限
4. 复制生成的 token

### 3. 运行第一个 Review

```bash
# 审查单个 PR
python cli.py review owner/repo 42

# 或使用完整 URL
python cli.py review https://github.com/owner/repo/pull/42
```

预期输出：
```
Reviewing PR: owner/repo#42
  [LLM] payload=15234 chars (~3808 tokens), msgs=3
  ...

## Code Review Complete
PR:       owner/repo#42
Decision: REQUEST_CHANGES
Issues:   3
Session:  abc123def456
Trace:    ./trajectories/trace_abc123def456.jsonl
```

### 4. 查看审查轨迹

```bash
# 查看详细的执行过程
python cli.py inspect ./trajectories/trace_abc123def456.jsonl
```

---

## 核心概念

### Agent（智能体）

Agent 是系统的核心执行单元，模拟 Claude Code 的执行模型。它通过循环调用 LLM 和工具来完成任务。

```
┌─────────────┐
│   Agent     │
│  ┌───────┐  │
│  │  LLM  │  │ ← 推理和决策
│  └───┬───┘  │
│      ↓      │
│  ┌───────┐  │
│  │ Tool  │  │ ← 执行具体操作
│  └───┬───┘  │
│      ↓      │
│  (记录轨迹) │
└─────────────┘
```

### Skill（技能）

Skill 是定义 Agent 行为的系统提示（System Prompt）。每个 Skill 对应一个特定的能力：

| Skill | 职责 | LLM 调用次数 |
|-------|------|-------------|
| `code-review` | 主入口，协调整个流程 | 路由 |
| `code-review-triage` | PR 分类，判断是否需要人工介入 | 1 次 |
| `code-review-analyze` | 并发采集 PR 数据 | 0 次 |
| `code-review-act` | 执行审查和自动修复 | 1 次 |
| `code-review-test-gen` | 自动生成单元测试 | 条件触发 |
| `code-review-memory` | 管理知识图谱 | 0 次 |

### Tool（工具）

Tool 是 Agent 可以调用的具体功能，分为两类：

**GitHub 工具**（10 个）：
- `get_pull_request` - 获取 PR 详情
- `get_pull_request_files` - 获取变更文件
- `create_pull_request_review` - 提交 Review
- `create_branch` - 创建分支
- `create_or_update_file` - 创建/更新文件
- `create_pull_request` - 创建 PR
- 等

**Memory 工具**（6 个）：
- `memory_get_all` - 获取所有规则
- `memory_add_pattern` - 添加问题模式
- `memory_get_developer_profile` - 获取开发者画像
- `memory_update_developer_profile` - 更新开发者画像
- `memory_aggregate_patterns` - 聚合仓库模式

### Trajectory（轨迹）

轨迹是 Agent 执行的完整记录，用于：
1. **调试分析** - 查看每一步的决策过程
2. **训练数据生成** - 导出为 SFT、工具监督、偏好对格式

轨迹文件格式（JSONL）：
```json
{"type": "session_start", "session_id": "abc123", "pr": "owner/repo#42"}
{"type": "user", "message": {"role": "user", "content": "review owner/repo #42"}}
{"type": "assistant", "message": {"role": "assistant", "content": [...], "stop_reason": "tool_use"}}
{"type": "session_end", "stats": {"llm_calls": 5, "tool_calls": 8}}
```

---

## 架构设计

### 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLI Layer                               │
│  ┌──────────┐ ┌─────────────┐ ┌─────────┐ ┌─────────────────┐  │
│  │  review  │ │batch-review │ │ export  │ │  health-report  │  │
│  └────┬─────┘ └──────┬──────┘ └────┬────┘ └────────┬────────┘  │
└───────┼──────────────┼─────────────┼───────────────┼───────────┘
        │              │             │               │
        └──────────────┴──────┬──────┴───────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    Pipeline Layer                               │
│              ReviewOrchestrator                                 │
│   - 构建组件 (LLMClient, ToolRouter, AgentRunner)              │
│   - 解析 PR 标识符                                              │
│   - 批量处理                                                    │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                     Agent Layer                                 │
│  ┌─────────────┐  ┌──────────┐  ┌────────────┐  ┌───────────┐  │
│  │ AgentRunner │  │LLMClient │  │ToolRouter  │  │SkillLoader│  │
│  │             │  │          │  │            │  │           │  │
│  │ - 主循环    │  │- Anthropic│  │- 工具分发  │  │- 加载     │  │
│  │ - 轨迹记录  │  │- OpenAI  │  │- 结果截断  │  │- 合并     │  │
│  └─────────────┘  └──────────┘  └────────────┘  └───────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                     Tools Layer                                 │
│  ┌──────────────┐           ┌─────────────────┐                │
│  │ GitHubTools  │           │   MemoryTools   │                │
│  │ - 10 个工具   │           │   - 6 个工具     │                │
│  │ - PyGithub   │           │   - JSONL 存储   │                │
│  └──────────────┘           └─────────────────┘                │
└─────────────────────────────────────────────────────────────────┘
```

### 数据流

```
用户输入 PR
    ↓
解析 PR 标识符 → (owner, repo, pr_number)
    ↓
Triage (1 LLM 调用)
    ├── human_required → 添加 Comment，结束
    └── auto → 继续
    ↓
Analyze (0 LLM 调用，并发采集)
    ├── get_pull_request
    ├── get_pull_request_files
    ├── list_commits
    └── memory_get_all
    ↓
Act (1 LLM 调用)
    ├── 分析代码问题
    ├── 提交 GitHub Review
    ├── 创建 Fix PR（如有 auto_fixable 问题）
    ├── 更新开发者画像
    └── 聚合仓库模式
    ↓
输出结果
```

### 模块关系

```python
# CLI → Orchestrator → AgentRunner → (LLMClient + ToolRouter + SkillLoader)
#                          ↓
#                   TrajectoryLogger
#                          ↓
#                    GitHubTools / MemoryTools
```

---

## 详细指南

### Agent 执行流程详解

**AgentRunner.run()** 方法是核心执行循环：

```python
def run(self, task: str, pr: str = "") -> AgentResult:
    # 1. 创建轨迹记录器
    with TrajectoryLogger(...) as logger:

        # 2. 初始化消息历史
        messages = [{"role": "user", "content": task}]

        # 3. 主循环（最多 30 轮）
        while iteration < MAX_ITERATIONS:
            # 3.1 调用 LLM
            response = llm_client.complete(messages, system, tools)

            # 3.2 记录 Assistant 响应
            logger.log_assistant(response)

            # 3.3 检查停止条件
            if response.stop_reason == "end_turn":
                break

            # 3.4 执行工具调用
            for tool_use in response.tool_uses:
                result = tool_router.execute(tool_use.name, tool_use.input)
                logger.log_tool_result(tool_use, result)

            # 3.5 更新消息历史
            messages.append({...})  # Assistant content
            messages.append({...})  # Tool results

        # 4. 提取决策和统计
        decision = extract_decision(messages)
        issues_found = extract_issues(messages)

        # 5. 关闭轨迹记录
        stats = logger.close(extra_stats={...})

    return AgentResult(...)
```

### Skill 加载机制

**SkillLoader** 负责从文件系统加载 Skill：

```python
# 加载单个 Skill（包含 references）
prompt = skill_loader.load_skill("code-review", include_references=True)

# 加载多个 Skill 并合并
combined_prompt = skill_loader.load_combined(
    "code-review",
    "code-review-triage",
    "code-review-analyze",
    "code-review-act",
    "code-review-memory"
)
```

Skill 文件结构：
```
skills/{skill-name}/
├── SKILL.md              # 主提示文件
└── references/
    ├── review-checklist.md
    ├── escalation-rules.md
    └── ...
```

### 工具路由机制

**ToolRouter** 维护工具名称到函数的映射：

```python
# 工具注册表
self._tools = {
    "get_pull_request": self.github_tools.get_pull_request,
    "create_pull_request_review": self.github_tools.create_pull_request_review,
    "memory_get_all": self.memory_tools.memory_get_all,
    # ...
}

# 执行工具调用
result = tool_router.execute("get_pull_request", {
    "owner": "anthropics",
    "repo": "claude-code",
    "pull_number": 42
})
```

### Memory 知识图谱

Memory 存储在 `~/.claude/review_memory.jsonl`，包含 6 种记录类型：

| 类型 | 用途 | 示例 |
|------|------|------|
| `known_pattern` | 已知问题模式 | SQL 注入、硬编码密钥 |
| `review_rule` | 团队代码规范 | "函数不超过 50 行" |
| `false_positive` | 误报排除规则 | "测试文件中的 mock 数据" |
| `fix_template` | 自动修复模板 | SQL 参数化查询模板 |
| `developer_profile` | 开发者成长画像 | 历史问题、成长领域 |
| `repo_pattern` | 仓库级问题统计 | 高频问题、趋势分析 |

### 开发者成长画像

每个开发者都有一个画像，记录跨 PR 的问题模式：

```json
{
  "type": "developer_profile",
  "content": {
    "author": "github_username",
    "issue_history": [
      {"category": "security", "count": 3, "last_seen": "2026-04-01"},
      {"category": "style", "count": 8, "last_seen": "2026-04-15"}
    ],
    "strengths": ["测试覆盖率高", "命名规范"],
    "growth_areas": ["SQL 安全", "错误处理"],
    "pr_count": 12
  }
}
```

**使用场景**：
- 新手（`pr_count < 5`）：comment 附带详细解释
- 中级（`5 ≤ pr_count < 20`）：简洁说明问题
- 资深（`pr_count ≥ 20`）：直接指出问题
- 有历史问题：在 Review summary 中提及成长建议

### 自动生成测试用例

当满足以下条件时，系统会自动生成单元测试：
1. 变更包含业务逻辑文件（非测试文件）
2. 新增代码超过 20 行
3. diff 包含新增的函数/方法

支持的测试框架：
- Python: pytest, unittest
- JavaScript/TypeScript: jest, mocha, vitest
- Java: JUnit
- Go: 内置 testing 包

---

## API 参考

### CLI 命令

```bash
# 审查单个 PR
python cli.py review owner/repo 42 [--dry-run] [--no-thinking]

# 批量审查
python cli.py batch-review --prs prs.txt [--workers 4]

# 导出训练数据
python cli.py export --input ./trajectories --output ./exports \
  [--format sft tool-supervision preference-pairs]

# 生成仓库健康报告
python cli.py health-report --repo owner/repo [--output report.md]

# 查看轨迹
python cli.py inspect trace_abc123.jsonl
```

### 配置选项

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `GITHUB_TOKEN` | GitHub API Token | 必填 |
| `ANTHROPIC_API_KEY` | Anthropic API Key | 必填 |
| `ANTHROPIC_MODEL` | 模型名称 | `claude-opus-4-6` |
| `TRAJECTORIES_DIR` | 轨迹输出目录 | `./trajectories` |

### GitHub 工具

| 工具名 | 参数 | 返回值 |
|--------|------|--------|
| `get_pull_request` | owner, repo, pull_number | PR 详情字典 |
| `get_pull_request_files` | owner, repo, pull_number | 变更文件列表 |
| `create_pull_request_review` | owner, repo, pull_number, event, body, comments | Review 对象 |
| `create_branch` | owner, repo, branch, from_branch | 分支引用 |
| `create_or_update_file` | owner, repo, path, content, message, branch, sha | 文件对象 |
| `create_pull_request` | owner, repo, title, head, base, body | PR 对象 |

### Memory 工具

| 工具名 | 参数 | 返回值 |
|--------|------|--------|
| `memory_get_all` | max_tokens | 格式化规则摘要 |
| `memory_add_pattern` | name, description, severity, indicators | 记录 ID |
| `memory_get_developer_profile` | author | 开发者画像 |
| `memory_update_developer_profile` | author, new_issues, strengths, growth_areas | 更新后的画像 |
| `memory_aggregate_patterns` | repo, issues | 更新的模式数量 |

---

## 最佳实践

### 1. 初次使用建议

**从小规模开始**：
```bash
# 先使用 dry-run 模式测试
python cli.py review owner/repo 42 --dry-run

# 检查轨迹文件，确认工具调用正确
python cli.py inspect ./trajectories/trace_xxx.jsonl
```

**逐步启用功能**：
1. 第一步：只审查，不提交 Review（dry-run）
2. 第二步：提交 COMMENT 级别的 Review
3. 第三步：启用 REQUEST_CHANGES
4. 第四步：启用自动修复（Fix PR）

### 2. 批量审查策略

```bash
# 创建 PR 列表文件
cat > prs.txt << EOF
anthropics/claude-code #123
anthropics/claude-code #124
anthropics/claude-code #125
EOF

# 批量审查（4 个并发）
python cli.py batch-review --prs prs.txt --workers 4
```

### 3. 训练数据导出

```bash
# 审查一段时间后，导出训练数据
python cli.py export \
  --input ./trajectories \
  --output ./exports \
  --format sft tool-supervision preference-pairs

# 输出文件
# - exports/sft_train.jsonl          # 监督微调数据
# - exports/tool_supervision.jsonl   # 工具调用训练数据
# - exports/preference_pairs.jsonl   # DPO 偏好对数据
```

### 4. 监控和调试

**查看执行轨迹**：
```bash
python cli.py inspect trace_abc123.jsonl
```

输出示例：
```
[0] SESSION_START  pr=owner/repo#42  id=abc123
[1] USER          'review owner/repo #42'
[2] ASSISTANT     stop=tool_use  in=1203  out=450  blocks=[tool_use(get_pull_request)]
[3] TOOL_RESULT   ids=[tool_01]
...
[15] SESSION_END   llm_calls=5  tool_calls=8  duration=45231ms  decision=REQUEST_CHANGES
```

### 5. 常见问题

**Q: 遇到 GitHub API 速率限制怎么办？**
A: 系统会自动重试，但建议：
- 使用 `--workers 1` 降低并发
- 检查 `GITHUB_TOKEN` 是否有效
- 批量审查时增加间隔时间

**Q: LLM 调用失败？**
A: 检查：
- `ANTHROPIC_API_KEY` 是否设置
- 网络连接是否正常
- 模型名称是否正确

**Q: 如何提高审查质量？**
A:
- 使用 `code-review-memory` 添加团队规则
- 定期导出轨迹并 fine-tune 模型
- 调整 `include_references=True` 加载更多上下文

---

## 扩展开发

### 添加新工具

1. 在 `tools/github_tools.py` 或 `tools/memory_tools.py` 添加方法
2. 在 `TOOL_DEFINITIONS` 中添加定义
3. 在 `agent/tool_router.py` 中注册

### 添加新 Skill

1. 创建 `skills/{skill-name}/SKILL.md`
2. 可选创建 `skills/{skill-name}/references/` 下的参考文件
3. 在 `agent/runner.py` 中使用 `load_combined()` 加载

### 自定义审查规则

```python
# 在 review 前添加自定义规则
python cli.py review owner/repo 42 --extra-context '{
  "custom_rules": [
    "必须使用类型注解",
    "函数必须有 docstring"
  ]
}'
```

---

## 贡献指南

欢迎提交 Issue 和 PR！请确保：
1. 代码符合 PEP 8 规范
2. 新增功能包含测试
3. 更新相关文档

---

## 许可证

[MIT License](LICENSE)

---

**文档版本**: 1.0
**最后更新**: 2026-04-21
