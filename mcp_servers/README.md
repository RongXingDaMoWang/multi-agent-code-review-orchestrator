# MCP Servers

把 Code Review Agent 的工具层封装为独立的 MCP (Model Context Protocol) Server，
任何兼容 MCP 的 Agent 都能通过统一协议发现并调用这些工具。

> 设计原则：MCP Server 是 **增量叠加层**，不修改 `tools/` 目录的任何代码——
> 底层实现继续复用 `tools/github_tools.py` 与 `tools/memory_tools.py`。

## 服务器一览

| 名称 | 文件 | 工具数 | 来源 |
|---|---|---|---|
| `git` | `git_mcp_server.py` | 11 | 封装 `tools/github_tools.py` |
| `memory` | `memory_mcp_server.py` | 6 | 封装 `tools/memory_tools.py` |
| `code_analysis` | `code_analysis_mcp_server.py` | 3 | 新增 AST/正则分析能力 |

### `git` — GitHub 操作

- `get_pull_request(owner, repo, pull_number)`
- `get_pull_request_files(owner, repo, pull_number)` — patch 截断 3000 字符/文件
- `list_commits(owner, repo, sha=None, per_page=10)`
- `get_file_contents(owner, repo, path, branch=None)` — 内容截断 5000 字符
- `create_pull_request_review(owner, repo, pull_number, event, body, comments=None)`
- `create_branch(owner, repo, branch, from_branch=None)`
- `create_or_update_file(owner, repo, path, content, message, branch, sha=None)`
- `create_pull_request(owner, repo, title, head, base, body="")`
- `add_issue_comment(owner, repo, issue_number, body)`
- `get_pull_request_status(owner, repo, pull_number)`
- `detect_test_framework(owner, repo, branch=None)`

### `memory` — review_memory.jsonl 读写

- `search_known_patterns(max_tokens=3000)` — 汇总规则/模式/误报
- `add_pattern(name, description, severity="high", indicators=None)`
- `mark_false_positive(pattern, reason, file_patterns=None)`
- `get_developer_profile(author)`
- `update_developer_profile(author, new_issues=None, strengths=None, growth_areas=None)`
- `aggregate_repo_patterns(repo, issues)`

### `code_analysis` — 静态分析

- `analyze_complexity(code)` — McCabe 圈复杂度，按函数列表，`>=10` 标记 ⚠️
- `detect_security_issues(code, language="python")` — 基于正则的安全扫描（python/javascript）
- `extract_functions(code)` — 提取 Python 函数签名

## 安装

```bash
pip install -r mcp_servers/requirements.txt
# 加上根目录的 requirements.txt（PyGithub 等）
pip install -r requirements.txt
```

## 独立运行

每个 Server 都能通过 stdio 协议独立启动：

```bash
# 需要 GitHub 操作时：
export GITHUB_TOKEN=ghp_xxx       # Windows: set GITHUB_TOKEN=ghp_xxx
python mcp_servers/git_mcp_server.py

python mcp_servers/memory_mcp_server.py
python mcp_servers/code_analysis_mcp_server.py
```

启动后 Server 阻塞在 stdio，等待 MCP 客户端的 JSON-RPC 请求。

## 在 Agent 中注册

见项目根目录 `.kiro/settings/mcp.json` —— 三个 Server 已注册完成，
Kiro / Claude Desktop / 任意 MCP 客户端读取该文件即可加载。
