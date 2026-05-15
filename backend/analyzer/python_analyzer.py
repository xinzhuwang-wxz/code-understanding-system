from __future__ import annotations

import ast
import os
from pathlib import Path

from .graph import Graph, Node, Edge


ENDPOINT_DECORATORS = {
    "route", "get", "post", "put", "patch", "delete", "head", "options",
    "api_view", "action",
    "websocket",
}

ROUTER_PATTERNS = {
    "APIRouter", "Blueprint", "Router", "Namespace",
    "DefaultRouter", "SimpleRouter",
}

MODEL_BASES = {
    "Model", "models.Model", "db.Model", "Base",
    "Document", "EmbeddedDocument",
    "ModelBase",
}

TASK_DECORATORS = {
    "task", "shared_task", "periodic_task",
    "job", "background_task",
}

DB_READ_METHODS = {
    "filter", "get", "all", "first", "last", "count",
    "exists", "aggregate", "values", "values_list",
    "select_related", "prefetch_related", "annotate",
    "order_by", "distinct", "exclude",
    "find", "find_one", "find_many",
    "query", "execute", "fetchone", "fetchall", "fetchmany",
    "select", "where",
}

DB_WRITE_METHODS = {
    "save", "create", "update", "delete", "bulk_create",
    "bulk_update", "insert", "insert_one", "insert_many",
    "update_one", "update_many", "delete_one", "delete_many",
    "commit", "add", "merge", "flush",
    "put_item", "delete_item",
}

API_CALL_NAMES = {
    "get", "post", "put", "patch", "delete", "head", "options",
    "request", "fetch", "urlopen",
}

API_CALL_MODULES = {
    "requests", "httpx", "aiohttp", "urllib",
}


class PythonAnalyzer:
    def analyze_file(self, file_info: dict, graph: Graph) -> None:
        try:
            with open(file_info["full_path"], "r", encoding="utf-8", errors="ignore") as f:
                source = f.read()
        except (OSError, UnicodeDecodeError):
            return

        try:
            tree = ast.parse(source, filename=file_info["rel_path"])
        except SyntaxError:
            return

        file_id = f"file:{file_info['rel_path']}"
        self._extract_classes(tree, file_info, file_id, graph)
        self._extract_functions(tree, file_info, file_id, graph)
        self._extract_imports(tree, file_info, file_id, graph)
        self._extract_calls(tree, file_info, file_id, graph, source)

    def _get_decorator_names(self, node: ast.AST) -> list[str]:
        names = []
        for dec in getattr(node, "decorator_list", []):
            if isinstance(dec, ast.Name):
                names.append(dec.id)
            elif isinstance(dec, ast.Attribute):
                names.append(dec.attr)
            elif isinstance(dec, ast.Call):
                if isinstance(dec.func, ast.Name):
                    names.append(dec.func.id)
                elif isinstance(dec.func, ast.Attribute):
                    names.append(dec.func.attr)
        return names

    def _get_base_names(self, node: ast.ClassDef) -> list[str]:
        names = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                names.append(base.id)
            elif isinstance(base, ast.Attribute):
                names.append(f"{self._attr_chain(base)}")
        return names

    def _attr_chain(self, node: ast.Attribute) -> str:
        parts = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))

    def _extract_classes(self, tree: ast.Module, file_info: dict, file_id: str, graph: Graph) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            class_id = f"class:{file_info['rel_path']}:{node.name}"
            decorators = self._get_decorator_names(node)
            bases = self._get_base_names(node)

            node_type = "class"
            if any(b in MODEL_BASES for b in bases):
                node_type = "model"
            elif any(name in ROUTER_PATTERNS for name in [node.name] + bases):
                node_type = "router"
            elif "service" in node.name.lower():
                node_type = "service"
            elif "middleware" in node.name.lower():
                node_type = "middleware"
            elif "util" in node.name.lower() or "helper" in node.name.lower():
                node_type = "utility"

            docstring = ast.get_docstring(node)
            metadata = {"bases": bases, "decorators": decorators}
            if docstring:
                metadata["docstring"] = docstring

            graph.add_node(Node(
                id=class_id,
                label=node.name,
                type=node_type,
                file_path=file_info["rel_path"],
                line_number=node.lineno,
                metadata=metadata,
            ))
            graph.add_edge(Edge(source=file_id, target=class_id, type="uses"))

            for base in bases:
                if base not in ("object", "type", "ABC", "Exception"):
                    inherit_id = f"class_ref:{base}"
                    graph.add_node(Node(
                        id=inherit_id, label=base, type="class",
                        file_path="", metadata={"external_ref": True},
                    ))
                    graph.add_edge_deferred(Edge(
                        source=class_id, target=inherit_id, type="inherits",
                    ))

            for item in ast.walk(node):
                if isinstance(item, ast.FunctionDef) or isinstance(item, ast.AsyncFunctionDef):
                    method_decorators = self._get_decorator_names(item)
                    if any(d in ENDPOINT_DECORATORS for d in method_decorators):
                        ep_id = f"endpoint:{file_info['rel_path']}:{node.name}.{item.name}"
                        graph.add_node(Node(
                            id=ep_id,
                            label=f"{node.name}.{item.name}",
                            type="endpoint",
                            file_path=file_info["rel_path"],
                            line_number=item.lineno,
                            metadata={"method": self._guess_http_method(method_decorators)},
                        ))
                        graph.add_edge(Edge(source=class_id, target=ep_id, type="endpoint_handler"))

    def _extract_functions(self, tree: ast.Module, file_info: dict, file_id: str, graph: Graph) -> None:
        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            decorators = self._get_decorator_names(node)
            func_id = f"func:{file_info['rel_path']}:{node.name}"

            if any(d in ENDPOINT_DECORATORS for d in decorators):
                node_type = "endpoint"
            elif any(d in TASK_DECORATORS for d in decorators):
                node_type = "task"
            elif "middleware" in node.name.lower():
                node_type = "middleware"
            else:
                node_type = "function"

            docstring = ast.get_docstring(node)
            metadata = {"decorators": decorators, "is_async": isinstance(node, ast.AsyncFunctionDef)}
            if docstring:
                metadata["docstring"] = docstring

            graph.add_node(Node(
                id=func_id,
                label=node.name,
                type=node_type,
                file_path=file_info["rel_path"],
                line_number=node.lineno,
                metadata=metadata,
            ))
            graph.add_edge(Edge(source=file_id, target=func_id, type="uses"))

            if node_type == "endpoint":
                graph.add_edge(Edge(source=file_id, target=func_id, type="endpoint_handler"))

    def _extract_imports(self, tree: ast.Module, file_info: dict, file_id: str, graph: Graph) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    graph.add_edge_deferred(Edge(
                        source=file_id,
                        target=f"module:{alias.name}",
                        type="imports",
                    ))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    graph.add_edge_deferred(Edge(
                        source=file_id,
                        target=f"module:{node.module}",
                        type="imports",
                    ))

    def _extract_calls(self, tree: ast.Module, file_info: dict, file_id: str, graph: Graph, source: str) -> None:
        # Build class line ranges for self-call resolution
        class_ranges: dict[str, tuple[int, int]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                end_line = node.end_lineno if hasattr(node, "end_lineno") and node.end_lineno else 99999
                class_ranges[node.name] = (node.lineno, end_line)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            line_number = node.lineno

            if isinstance(node.func, ast.Attribute):
                method = node.func.attr
                obj_name = ""
                if isinstance(node.func.value, ast.Name):
                    obj_name = node.func.value.id

                if method in DB_READ_METHODS:
                    graph.add_edge_deferred(Edge(
                        source=file_id,
                        target=file_id,
                        type="db_read",
                        metadata={"method": method, "object": obj_name, "line_number": line_number},
                    ))
                elif method in DB_WRITE_METHODS:
                    graph.add_edge_deferred(Edge(
                        source=file_id,
                        target=file_id,
                        type="db_write",
                        metadata={"method": method, "object": obj_name, "line_number": line_number},
                    ))

                if obj_name in API_CALL_MODULES and method in API_CALL_NAMES:
                    graph.add_edge_deferred(Edge(
                        source=file_id,
                        target=file_id,
                        type="api_call",
                        metadata={"method": method, "module": obj_name, "line_number": line_number},
                    ))

                # Self.method() creates INVOKES edges for intra-class method calls
                if obj_name == "self":
                    enclosing_class = self._find_enclosing_class(node.lineno, class_ranges)
                    if enclosing_class:
                        method_ref_id = f"method_ref:{file_info['rel_path']}:{enclosing_class}.{method}"
                        graph.add_node(Node(
                            id=method_ref_id,
                            label=f"{enclosing_class}.{method}",
                            type="method",
                            file_path=file_info["rel_path"],
                            metadata={"external_ref": True},
                        ))
                        graph.add_edge_deferred(Edge(
                            source=file_id,
                            target=method_ref_id,
                            type="invokes",
                            metadata={
                                "method": method,
                                "class": enclosing_class,
                                "line_number": line_number,
                            },
                        ))

            elif isinstance(node.func, ast.Name):
                func_name = node.func.id

                if func_name in ("fetch", "urlopen"):
                    graph.add_edge_deferred(Edge(
                        source=file_id,
                        target=file_id,
                        type="api_call",
                        metadata={"function": func_name, "line_number": line_number},
                    ))

                # Create INVOKES edge for all function calls (resolution happens later)
                func_ref_id = f"function_ref:{func_name}"
                graph.add_node(Node(
                    id=func_ref_id,
                    label=func_name,
                    type="function",
                    file_path="",
                    metadata={"external_ref": True},
                ))
                graph.add_edge_deferred(Edge(
                    source=file_id,
                    target=func_ref_id,
                    type="invokes",
                    metadata={"function": func_name, "line_number": line_number},
                ))

    @staticmethod
    def _find_enclosing_class(line: int, class_ranges: dict[str, tuple[int, int]]) -> str | None:
        """Find the class name that encloses the given line number."""
        for class_name, (start, end) in class_ranges.items():
            if start <= line <= end:
                return class_name
        return None

    def _guess_http_method(self, decorators: list[str]) -> str:
        for d in decorators:
            dl = d.lower()
            if dl in ("get", "post", "put", "patch", "delete", "head", "options"):
                return dl.upper()
        return "GET"

    def resolve_imports(self, python_files: list[dict], graph: Graph) -> None:
        """Resolve module references to actual file nodes."""
        module_map: dict[str, str] = {}
        suffix_map: dict[str, str] = {}
        for f in python_files:
            rel = f["rel_path"]
            mod = rel.replace("/", ".").replace("\\", ".")
            if mod.endswith(".py"):
                mod = mod[:-3]
            if mod.endswith(".__init__"):
                mod = mod[:-9]
            file_id = f"file:{rel}"
            module_map[mod] = file_id

            parts = mod.split(".")
            for i in range(1, len(parts) + 1):
                suffix = ".".join(parts[-i:])
                suffix_map.setdefault(suffix, file_id)

        for edge in graph.edges:
            if edge.type == "imports" and edge.target.startswith("module:"):
                mod_name = edge.target[len("module:"):]
                if mod_name in module_map:
                    edge.target = module_map[mod_name]
                elif mod_name in suffix_map:
                    edge.target = suffix_map[mod_name]
                else:
                    parts = mod_name.split(".")
                    for i in range(len(parts), 0, -1):
                        prefix = ".".join(parts[:i])
                        if prefix in module_map:
                            edge.target = module_map[prefix]
                            break
                        if prefix in suffix_map:
                            edge.target = suffix_map[prefix]
                            break
