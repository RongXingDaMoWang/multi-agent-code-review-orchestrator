"""
MCP Server providing static code-analysis utilities.

These tools are NEW capabilities (no dependency on tools/) — pure stdlib
implementations meant to be cheap, local, and called by any reviewer
agent before invoking the LLM.

Run standalone:
    python mcp_servers/code_analysis_mcp_server.py
"""
import ast
import re

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("code_analysis")


_DECISION_NODES = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp,
)


class _CyclomaticVisitor(ast.NodeVisitor):
    """McCabe cyclomatic complexity: 1 + #decision points."""

    def __init__(self) -> None:
        self.cc = 1

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, _DECISION_NODES):
            self.cc += 1
        elif isinstance(node, ast.BoolOp):
            self.cc += max(0, len(node.values) - 1)
        super().generic_visit(node)


@mcp.tool()
def analyze_complexity(code: str) -> str:
    """Compute McCabe cyclomatic complexity per function in a Python
    source string. Returns a markdown table with function, line, and CC.
    A module-level score is included for top-level code."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"SyntaxError: {e.msg} (line {e.lineno})"

    rows: list[tuple[str, int, int]] = []
    module_visitor = _CyclomaticVisitor()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_visitor.visit(node)
    rows.append(("<module>", 1, module_visitor.cc))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            v = _CyclomaticVisitor()
            for child in node.body:
                v.visit(child)
            rows.append((node.name, node.lineno, v.cc))

    lines = ["| function | line | complexity |", "|---|---|---|"]
    for name, lineno, cc in rows:
        flag = " ⚠️" if cc >= 10 else ""
        lines.append(f"| {name} | {lineno} | {cc}{flag} |")
    return "\n".join(lines)


_PY_SECURITY_RULES = [
    (r"\beval\s*\(", "use of eval()", "high"),
    (r"\bexec\s*\(", "use of exec()", "high"),
    (r"pickle\.loads?\s*\(", "pickle deserialization (RCE risk)", "high"),
    (r"subprocess\.[A-Za-z_]+\([^)]*shell\s*=\s*True", "shell=True in subprocess", "high"),
    (r"\bos\.system\s*\(", "os.system shell invocation", "high"),
    (r"hashlib\.(md5|sha1)\s*\(", "weak hash algorithm", "medium"),
    (r"(?i)(password|api_key|secret|token)\s*=\s*['\"][^'\"]{4,}['\"]", "possible hardcoded secret", "critical"),
    (r"f['\"][^'\"]*(SELECT|INSERT|UPDATE|DELETE)[^'\"]*\{", "f-string SQL (injection risk)", "critical"),
    (r"verify\s*=\s*False", "TLS verification disabled", "high"),
]

_JS_SECURITY_RULES = [
    (r"\beval\s*\(", "use of eval()", "high"),
    (r"\.innerHTML\s*=", "innerHTML assignment (XSS risk)", "high"),
    (r"dangerouslySetInnerHTML", "React dangerouslySetInnerHTML (XSS risk)", "high"),
    (r"document\.write\s*\(", "document.write (XSS risk)", "high"),
    (r"new\s+Function\s*\(", "Function() constructor (eval-like)", "high"),
    (r"(?i)(password|apikey|api_key|secret|token)\s*[:=]\s*['\"][^'\"]{4,}['\"]", "possible hardcoded secret", "critical"),
]


@mcp.tool()
def detect_security_issues(code: str, language: str = "python") -> str:
    """Regex-based first-pass security scan. language ∈ {python, javascript}.
    Returns a markdown list of findings with line, severity, and rule."""
    lang = language.lower()
    if lang in ("python", "py"):
        rules = _PY_SECURITY_RULES
    elif lang in ("javascript", "js", "typescript", "ts"):
        rules = _JS_SECURITY_RULES
    else:
        return f"Unsupported language: {language} (try python or javascript)"

    findings: list[tuple[int, str, str, str]] = []
    for i, line in enumerate(code.splitlines(), start=1):
        for pattern, label, severity in rules:
            if re.search(pattern, line):
                findings.append((i, severity, label, line.strip()[:120]))

    if not findings:
        return "No issues detected."
    out = [f"Found {len(findings)} issue(s):"]
    for lineno, sev, label, snippet in findings:
        out.append(f"- L{lineno} [{sev}] {label} — `{snippet}`")
    return "\n".join(out)


@mcp.tool()
def extract_functions(code: str) -> str:
    """Extract function/method signatures from Python source.
    Returns one signature per line with `file:line  def name(args) -> ret`."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"SyntaxError: {e.msg} (line {e.lineno})"

    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kw = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            try:
                args = ast.unparse(node.args)
                ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
            except Exception:
                args, ret = "...", ""
            out.append(f"L{node.lineno}  {kw} {node.name}({args}){ret}")
    return "\n".join(out) if out else "No functions found."


if __name__ == "__main__":
    mcp.run()
