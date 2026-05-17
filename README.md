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
