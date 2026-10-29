"""The two testers never mix: no import path (direct or transitive) crosses quantum <-> tradingview."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIDES = {"quantum": ("market_tester.quantum", "strategies.quantum"),
         "tradingview": ("market_tester.tradingview", "strategies.tradingview")}


def module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imports(path: Path) -> set[str]:
    """Absolute names of the repo modules this file imports (relative imports resolved)."""
    me = module_name(path).split(".")
    package = me if path.name == "__init__.py" else me[:-1]
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join(package[:len(package) - node.level + 1] + ([base] if base else []))
            out.add(base)
            out.update(f"{base}.{a.name}" for a in node.names)
    return {m for m in out if m.split(".")[0] in ("market_tester", "strategies")}


def graph() -> dict[str, set[str]]:
    files = [*(ROOT / "market_tester").rglob("*.py"), *(ROOT / "strategies").rglob("*.py")]
    known = {module_name(f): f for f in files}
    return {name: {m for m in imports(f) if m in known} for name, f in known.items()}


def reach(g: dict[str, set[str]], start: str) -> set[str]:
    seen, todo = set(), [start]
    while todo:
        for nxt in g.get(todo.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen


def side_of(module: str) -> str | None:
    for side, prefixes in SIDES.items():
        if any(module == p or module.startswith(p + ".") for p in prefixes):
            return side
    return None


def test_no_import_crosses_the_testers():
    g = graph()
    bad = []
    for module in g:
        mine = side_of(module)
        for dep in reach(g, module):
            other = side_of(dep)
            if module.startswith("market_tester.common") and other is not None:
                bad.append(f"{module} (common) -> {dep}")
            elif mine and other and other != mine:
                bad.append(f"{module} -> {dep}")
    assert not bad, "\n".join(bad)


def test_graph_sees_known_edges():
    g = graph()
    assert "market_tester.quantum.engine" in reach(g, "strategies.quantum.orb_situations")
    assert "market_tester.common.config" in reach(g, "market_tester.quantum.engine")
