"""
MCP Server connectivity test.

Verifies that each MCP Server can be imported, lists its registered tools,
and runs a sample invocation of the code_analysis server (no network needed).

Usage:
    python demo/run_mcp_server_test.py
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Stub mcp so we can import servers without installing the mcp package
_fake_mcp_mod = types.ModuleType("mcp")
_fake_server = types.ModuleType("mcp.server")
_fake_fastmcp = types.ModuleType("mcp.server.fastmcp")


class _FakeFastMCP:
    """Minimal stub that captures @mcp.tool() decorated functions."""

    def __init__(self, name: str):
        self.name = name
        self._tools: dict[str, callable] = {}

    def tool(self):
        def decorator(fn):
            self._tools[fn.__name__] = fn
            return fn
        return decorator

    def run(self):
        pass

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())


_fake_fastmcp.FastMCP = _FakeFastMCP
_fake_mcp_mod.server = _fake_server
_fake_server.fastmcp = _fake_fastmcp
sys.modules["mcp"] = _fake_mcp_mod
sys.modules["mcp.server"] = _fake_server
sys.modules["mcp.server.fastmcp"] = _fake_fastmcp

# Stub github if not installed
try:
    import github  # noqa: F401
except ImportError:
    gh_mod = types.ModuleType("github")
    gh_mod.Github = lambda *a, **k: None
    gh_mod.GithubException = Exception
    sys.modules["github"] = gh_mod


def main():
    print("=" * 60)
    print("  MCP Server Connectivity Test")
    print("=" * 60)

    # ── 1. Git MCP Server ─────────────────────────────────────────────────
    print("\n[1] git_mcp_server.py")
    from mcp_servers import git_mcp_server  # noqa: E402
    git_tools = git_mcp_server.mcp.list_tools()
    print(f"    Registered tools ({len(git_tools)}):")
    for t in git_tools:
        print(f"      - {t}")
    assert len(git_tools) == 11, f"Expected 11, got {len(git_tools)}"
    print("    PASS")

    # ── 2. Memory MCP Server ──────────────────────────────────────────────
    print("\n[2] memory_mcp_server.py")
    from mcp_servers import memory_mcp_server  # noqa: E402
    mem_tools = memory_mcp_server.mcp.list_tools()
    print(f"    Registered tools ({len(mem_tools)}):")
    for t in mem_tools:
        print(f"      - {t}")
    assert len(mem_tools) == 6, f"Expected 6, got {len(mem_tools)}"
    print("    PASS")

    # ── 3. Code Analysis MCP Server ───────────────────────────────────────
    print("\n[3] code_analysis_mcp_server.py")
    from mcp_servers import code_analysis_mcp_server as ca  # noqa: E402
    ca_tools = ca.mcp.list_tools()
    print(f"    Registered tools ({len(ca_tools)}):")
    for t in ca_tools:
        print(f"      - {t}")
    assert len(ca_tools) == 3, f"Expected 3, got {len(ca_tools)}"

    # ── 4. Sample invocations (code_analysis — no network) ────────────────
    print("\n[4] Sample tool invocations:")

    sample_code = '''
def process_payment(user_input):
    query = f"SELECT * FROM orders WHERE id='{user_input}'"
    result = eval(user_input)
    password = "super-secret-123"
    for i in range(len(items)):
        for j in range(len(items[i])):
            if items[i][j] > threshold:
                total += items[i][j]
    return total
'''
    print("\n    analyze_complexity:")
    complexity = ca.analyze_complexity(sample_code)
    print("    " + complexity.replace("\n", "\n    "))

    print("\n    detect_security_issues:")
    security = ca.detect_security_issues(sample_code, "python")
    print("    " + security.replace("\n", "\n    "))

    print("\n    extract_functions:")
    funcs = ca.extract_functions(sample_code)
    print("    " + funcs.replace("\n", "\n    "))

    print("\n" + "=" * 60)
    print("  ALL MCP SERVER TESTS PASSED")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
