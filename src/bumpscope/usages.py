import ast
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Usage:
    # Full dotted path the code refers to, resolved through its imports, e.g. `httpx.Client`.
    path: str
    file: Path
    line: int
    # "call" when the reference is called right there, otherwise "reference".
    kind: str = "reference"
    positional_count: int = 0
    keywords: frozenset[str] = field(default_factory=frozenset)
    # `f(*args)` or `f(**kwargs)`: the call might pass any parameter.
    unpacks_args: bool = False
    unpacks_kwargs: bool = False


def _bindings(tree: ast.Module) -> dict[str, str]:
    """Map each name an absolute import binds to the dotted path it stands for.

    Scopes are not tracked: an import inside a function counts for the whole file.
    """
    bindings = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bindings[alias.asname] = alias.name
                else:
                    # `import a.b` binds `a`.
                    root = alias.name.split(".", 1)[0]
                    bindings[root] = root
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                if alias.name != "*":
                    bindings[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return bindings


class _Visitor(ast.NodeVisitor):
    def __init__(self, file: Path, bindings: dict[str, str], roots: set[str]) -> None:
        self.file = file
        self.bindings = bindings
        self.roots = roots
        self.usages: list[Usage] = []

    def _wanted(self, path: str) -> bool:
        return path.split(".", 1)[0] in self.roots

    def _resolve(self, node: ast.expr) -> str | None:
        """`h.Client` with `import httpx as h` resolves to `httpx.Client`."""
        attributes = []
        while isinstance(node, ast.Attribute):
            attributes.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name) or node.id not in self.bindings:
            return None
        return ".".join([self.bindings[node.id], *reversed(attributes)])

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if self._wanted(alias.name):
                self.usages.append(Usage(alias.name, self.file, node.lineno))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level or not node.module or not self._wanted(node.module):
            return
        for alias in node.names:
            path = node.module if alias.name == "*" else f"{node.module}.{alias.name}"
            self.usages.append(Usage(path, self.file, node.lineno))

    def visit_Call(self, node: ast.Call) -> None:
        path = self._resolve(node.func)
        if path is None or not self._wanted(path):
            self.generic_visit(node)
            return
        self.usages.append(
            Usage(
                path,
                self.file,
                node.lineno,
                kind="call",
                positional_count=sum(not isinstance(a, ast.Starred) for a in node.args),
                keywords=frozenset(k.arg for k in node.keywords if k.arg is not None),
                unpacks_args=any(isinstance(a, ast.Starred) for a in node.args),
                unpacks_kwargs=any(k.arg is None for k in node.keywords),
            )
        )
        # The callee is recorded; its arguments may hold more usages.
        for child in [*node.args, *node.keywords]:
            self.visit(child)

    def _reference(self, node: ast.expr) -> None:
        path = self._resolve(node)
        if path is not None and self._wanted(path):
            self.usages.append(Usage(path, self.file, node.lineno))
        else:
            self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._reference(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self._reference(node)


def find_usages(files: list[Path], roots: set[str], project: Path) -> list[Usage]:
    """Usages of anything under the top-level modules `roots`, in the given files.

    Files are reported relative to `project`. Files that do not parse are skipped.
    """
    usages = []
    for file in files:
        try:
            tree = ast.parse(file.read_bytes(), filename=str(file))
        except (SyntaxError, ValueError):
            continue
        visitor = _Visitor(file.relative_to(project), _bindings(tree), roots)
        visitor.visit(tree)
        usages.extend(visitor.usages)
    return usages
