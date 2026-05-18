# Multi-Agent Code Review Orchestration Platform

基于 Leader-Worker-Reviewer 三角色编排 + MCP 协议的智能代码审查系统。

## 架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CLI (cli.py)                                 │
│                  --mode single | multi                               │
└────────────────────────────┬────────────────────────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              │                             │
              ▼                             ▼
┌──────────────────────┐     ┌──────────────────────────────────────┐
│  Single-Agent Mode   │     │       Multi-Agent Mode               │
│  (AgentRunner 30轮)  │     │                                      │
│  review()            │     │  ┌────────────────────────────────┐  │
└──────────────────────┘     │  │        LeaderAgent             │  │
                             │  │  decompose → assign → synthesize│  │
                             │  └──────────┬─────────────────────┘  │
                             │             │                         │
                             │  ┌──────────▼─────────────────────┐  │
                             │  │     TaskScheduler (asyncio)     │  │
                             │  │  semaphore=3, timeout=60s/task  │  │
                             │  └──┬───────┬───────┬───────┬─────┘  │
                             │     │       │       │       │         │
                             │     ▼       ▼       ▼       ▼         │
                             │  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐    │
                             │  │ Sec │ │Perf │ │Arch │ │Style│    │
                             │  │ 10轮│ │ 10轮│ │ 10轮│ │ 10轮│    │
                             │  └──┬──┘ └──┬──┘ └──┬──┘ └──┬──┘    │
                             │     └───────┴───────┴───────┘         │
                             │             │                         │
                             │  ┌──────────▼─────────────────────┐  │
                             │  │      ReviewerAgent             │  │
                             │  │  conflict detect + retry list  │  │
                             │  └────────────────────────────────┘  │
                             └──────────────────────────────────────┘
                                           │
              ┌────────────────────────────┼────────────────────────┐
              │                            │                        │
              ▼                            ▼                        ▼
┌──────────────────┐        ┌──────────────────┐      ┌────────────────┐
│  MCP: git        │        │  MCP: memory     │      │ MCP: analysis  │
│  (11 tools)      │        │  (6 tools)       │      │ (3 tools)      │
└──────────────────┘        └──────────────────┘      └────────────────┘
              │                            │                        │
              ▼                            ▼                        ▼
┌──────────────────┐        ┌──────────────────┐      ┌────────────────┐
│ tools/           │        │ tools/           │      │ ast + regex    │
│ github_tools.py  │        │ memory_tools.py  │      │ (stdlib only)  │
└──────────────────┘        └──────────────────┘      └────────────────┘
```

## 快速开始

### 安装

```bash
cd code_review_agent
pip install -r requirements.txt
pip install -r mcp_servers/requirements.txt
```

### 单 Agent 模式（默认，向后兼容）

```bash
export GITHUB_TOKEN=ghp_xxx
export ANTHROPIC_API_KEY=sk-ant-xxx

python cli.py review owner/repo 42
python cli.py review https://github.com/owner/repo/pull/42
```

### 多 Agent 模式（Leader-Worker-Reviewer 编排）

```bash
python cli.py review owner/repo 42 --mode multi
```

多 Agent 模式下，系统会：
1. Leader 根据 PR 文件类型和变更规模拆解子任务
2. 并行派发 Worker（security / performance / architecture / style）
3. Reviewer 做质量控制（冲突检测、置信度评估）
4. 失败 Worker 自动重试一次
5. Leader 汇总最终 Review 结论

### 批量审查

```bash
python cli.py batch-review --prs prs.txt --workers 3
```

### 健康报告

```bash
python cli.py health-report --repo owner/repo
```

### 轨迹导出（用于微调）

```bash
python cli.py export --input ./trajectories --format sft tool-supervision
```

## 飞书集成（Feishu Integration）

Code Review 结果可自动推送到飞书群聊，支持 PR 审查摘要、开发者画像、健康报告三类消息。

### 前置条件

1. **安装 lark-cli**：
   ```bash
   npx @larksuite/cli
   ```

2. **创建飞书自建应用**：
   - 在 [飞书开发者后台](https://open.feishu.cn) 创建应用
   - 开通 `im:message:send_as_bot` 权限
   - 发布应用并获取 App ID / App Secret

3. **配置 lark-cli 认证**：
   ```bash
   lark-cli config init
   ```
   按交互式向导填入 App ID、App Secret 等信息。

4. **将 Bot 添加到目标群聊**：
   - 在飞书群设置中添加你创建的应用 Bot

### 使用方式

**方式一：环境变量**
```bash
export FEISHU_ENABLED=true
export FEISHU_CHAT_ID=oc_xxxxxxxx
export GITHUB_TOKEN=ghp_xxx
export ANTHROPIC_API_KEY=sk-ant-xxx

# 审查完成后自动推送结果到飞书群
python cli.py review owner/repo 42 --mode multi
```

**方式二：CLI 参数**
```bash
python cli.py review owner/repo 42 --mode multi --feishu --feishu-chat-id oc_xxxxxxxx
```

### 推送的消息类型

| 消息 | 触发时机 | 内容 |
|------|---------|------|
| **PR Review Summary** | 每次 review 完成后 | 决策（APPROVE/REQUEST_CHANGES）、严重度分布、各 Worker 分析结果、TOP 5 发现、建议 |
| **Developer Profile** | 手动调用 `notify_review_result` 时 | 开发者 PR 数量、常见问题、强项、成长方向 |
| **Health Report** | 手动调用 `send_health_report` 时 | 仓库问题模式统计、严重度分布、高频文件 |

### 代码调用

```python
from code_review_agent.integrations.feishu_reporter import FeishuReporter, FeishuConfig

config = FeishuConfig(enabled=True, chat_id="oc_xxx")
reporter = FeishuReporter(config)

# 发送审查摘要
reporter.send_review_summary(
    pr_label="owner/repo#42",
    decision="REQUEST_CHANGES",
    issues_found=8,
    session_id="abc123",
    stats={"severity_counts": {"critical": 2, "high": 3, "medium": 3}},
    multi_agent={...},
)

# 发送开发者画像
reporter.send_developer_profile(author="github-username")

# 发送健康报告
reporter.send_health_report(repo="owner/repo", report=markdown_report)
```

### 安全策略

- 飞书推送是 **best-effort**：推送失败不会中断 Code Review 主流程
- 所有 lark-cli 调用有 30 秒超时
- 不会在日志中打印 chat_id 等敏感信息

## MCP Server 层

将现有工具封装为独立 MCP Server，任何兼容 MCP 协议的 Agent 都能发现和调用：

| Server | 文件 | 工具数 | 来源 |
|--------|------|--------|------|
| `git` | `mcp_servers/git_mcp_server.py` | 11 | 封装 `tools/github_tools.py` |
| `memory` | `mcp_servers/memory_mcp_server.py` | 6 | 封装 `tools/memory_tools.py` |
| `code_analysis` | `mcp_servers/code_analysis_mcp_server.py` | 3 | 新增 AST/正则分析 |

独立启动：

```bash
python mcp_servers/git_mcp_server.py          # 需要 GITHUB_TOKEN
python mcp_servers/memory_mcp_server.py
python mcp_servers/code_analysis_mcp_server.py
```

注册配置见 `.kiro/settings/mcp.json`。

## Skill Registry 动态发现

`orchestration/skill_registry.py` 提供运行时 Skill 和 MCP 工具发现：

```python
from orchestration.skill_registry import SkillRegistry

registry = SkillRegistry()
skills = registry.discover_skills()          # 扫描 skills/*/SKILL.md
tools  = registry.discover_mcp_tools()       # 解析 mcp.json + AST 提取工具名

# 按 Worker role 匹配
security_skills = registry.match_skills_for_role("security")
security_tools  = registry.match_tools_for_role("security")
```

每个 SKILL.md 的 frontmatter 包含 `tags` 字段，SkillRegistry 据此做 role→skill 匹配。

## 可观测性

### 编排轨迹

多 Agent 模式产出 `orch_<task_id>.jsonl`，包含 5 种事件：

- `orchestration_start` — 任务开始
- `subtask_dispatched` — Worker 分配
- `worker_completed` — Worker 完成（含 duration/tokens/findings）
- `reviewer_verdict` — Reviewer 判定
- `orchestration_end` — 任务结束（含汇总 severity）

### Metrics

```python
from orchestration.metrics import OrchestrationMetrics
# metrics.print_summary() 输出人类可读摘要
```

### 轨迹导出

```python
from trajectory.exporter import export_orchestration_trace
trace = export_orchestration_trace(session_id="abc123", trajectories_dir=Path("./trajectories"))
# 返回嵌套 dict，适合前端可视化
```

## 设计决策

### 为什么用 Leader-Worker-Reviewer？

| 考量 | 决策 |
|------|------|
| 单 Agent 30 轮循环 context 膨胀 | Worker 各自 10 轮，互不共享上下文 |
| 安全/性能/架构/风格关注点混杂 | 每个 Worker 有独立 system prompt 聚焦 |
| 单点失败阻塞整个 review | Scheduler 隔离失败，Reviewer 触发重试 |
| 结果质量无保障 | Reviewer 做冲突检测 + 置信度评估 |

### 为什么用 MCP？

- **协议标准化**：任何 MCP 客户端（Claude Desktop、Kiro、自定义 Agent）都能直接调用
- **增量叠加**：MCP Server 是新增层，`tools/` 目录零改动
- **可组合**：未来新增工具只需新建 Server，无需改动编排逻辑

### Skill 如何动态发现？

- 每个 SKILL.md 的 YAML frontmatter 包含 `tags: [...]`
- SkillRegistry 扫描 `skills/` 目录，解析 frontmatter
- Worker 初始化时按 role 匹配 tags，动态加载对应 Skill 子集
- MCP 工具通过 AST 解析 `@mcp.tool()` 装饰器自动枚举

## 目录结构

```
code_review_agent/
├── agent/                    # 核心 Agent 循环（未修改）
│   ├── runner.py             # AgentRunner 30 轮主循环
│   ├── llm_client.py         # OpenAI-compatible LLM 客户端
│   ├── tool_router.py        # 工具分发 + 截断
│   └── skill_loader.py       # SKILL.md 加载器
├── orchestration/            # 多智能体编排层（Phase 2+3 新增）
│   ├── leader.py             # LeaderAgent: decompose/assign/synthesize
│   ├── worker.py             # WorkerAgent: role-scoped bounded runner
│   ├── reviewer.py           # ReviewerAgent: conflict/confidence/retry
│   ├── scheduler.py          # asyncio TaskScheduler
│   ├── schemas.py            # SubTask/WorkerResult/FinalReview 等
│   ├── skill_registry.py     # Skill + MCP 工具动态发现
│   └── metrics.py            # OrchestrationMetrics 收集
├── mcp_servers/              # MCP Server 层（Phase 1 新增）
│   ├── git_mcp_server.py     # 11 个 GitHub 工具
│   ├── memory_mcp_server.py  # 6 个 Memory 工具
│   ├── code_analysis_mcp_server.py  # 3 个静态分析工具
│   └── requirements.txt
├── pipeline/                 # 流水线编排
│   └── orchestrator.py       # ReviewOrchestrator (single + multi)
├── skills/                   # Skill 定义（SKILL.md + references/）
│   ├── code-review/
│   ├── code-review-triage/
│   ├── code-review-analyze/
│   ├── code-review-act/
│   ├── code-review-memory/
│   └── code-review-test-gen/
├── integrations/              # 外部服务集成（新增）
│   └── feishu_reporter.py    # 飞书消息推送
├── tools/                    # 底层工具实现（未修改）
│   ├── github_tools.py
│   └── memory_tools.py
├── trajectory/               # 轨迹记录 + 导出
│   ├── logger.py             # TrajectoryLogger (含编排事件)
│   ├── schemas.py            # 数据结构（含 5 个编排 Record）
│   └── exporter.py           # SFT/DPO/编排轨迹导出
├── cli.py                    # CLI 入口
├── config.py                 # 环境变量配置
├── test_all.py               # 全量测试
└── .kiro/settings/mcp.json   # MCP Server 注册
```

## 运行测试

```bash
python test_all.py
```

测试覆盖 Bug 修复验证 + 新功能集成测试，不依赖网络（全部 mock）。
