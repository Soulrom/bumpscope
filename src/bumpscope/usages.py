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


def _parse(file: Path) -> ast.Module | None:
    """The file's syntax tree, or None if it cannot be read or does not parse."""
    try:
        return ast.parse(file.read_bytes(), filename=str(file))
    except (OSError, SyntaxError, ValueError):
        return None


def find_usages(files: list[Path], roots: set[str], project: Path) -> list[Usage]:
    """Usages of anything under the top-level modules `roots`, in the given files.

    Files are reported relative to `project`. Files that do not parse are skipped.
    """
    usages = []
    for file in files:
        tree = _parse(file)
        if tree is None:
            continue
        visitor = _Visitor(file.relative_to(project), _bindings(tree), roots)
        visitor.visit(tree)
        usages.extend(visitor.usages)
    return usages


@dataclass(frozen=True)
class ImportedModule:
    # What is imported, as far as the import names it: `google.cloud.storage` for
    # `from google.cloud import storage`, even though `storage` may be a class, not a module.
    path: str
    file: Path
    line: int
    # Inside a `try` whose handlers catch ImportError: an optional dependency.
    optional: bool = False


# Handlers that mark an optional dependency. Broad ones (`except Exception:`, a bare
# `except:`) do not: they usually guard the code around the import, not the import itself.
_IMPORT_ERRORS = {"ImportError", "ModuleNotFoundError"}


def _catches_import_error(node: ast.Try | ast.TryStar) -> bool:
    for handler in node.handlers:
        if handler.type is None:
            continue
        types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        if any(isinstance(t, ast.Name) and t.id in _IMPORT_ERRORS for t in types):
            return True
    return False


def _collect_imports(node: ast.AST, file: Path, optional: bool, found: list) -> None:
    if isinstance(node, ast.Import):
        found.extend(ImportedModule(a.name, file, node.lineno, optional) for a in node.names)
    elif isinstance(node, ast.ImportFrom):
        # Relative imports are the project's own code.
        if node.level == 0 and node.module:
            found.extend(
                ImportedModule(
                    node.module if a.name == "*" else f"{node.module}.{a.name}",
                    file,
                    node.lineno,
                    optional,
                )
                for a in node.names
            )
    elif isinstance(node, (ast.Try, ast.TryStar)):
        # Only the `try` body is guarded; imports in handlers, `else` and `finally` are not.
        guarded = optional or _catches_import_error(node)
        for child in node.body:
            _collect_imports(child, file, guarded, found)
        for child in [*node.handlers, *node.orelse, *node.finalbody]:
            _collect_imports(child, file, optional, found)
    else:
        for child in ast.iter_child_nodes(node):
            _collect_imports(child, file, optional, found)


def imported_modules(files: list[Path], project: Path) -> list[ImportedModule]:
    """Every absolute import in the given files. Files are reported relative to `project`."""
    found: list[ImportedModule] = []
    for file in files:
        tree = _parse(file)
        if tree is not None:
            _collect_imports(tree, file.relative_to(project), False, found)
    return found
