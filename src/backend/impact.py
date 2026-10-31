"""
Smart Change Impact Analyzer
============================

Answers: "If I modify this function, which other functions, files and
tests might be affected?"

Every relationship returned here comes from real source code, extracted
with Python's ``ast`` module. No LLM is involved and nothing is executed.
Each edge carries evidence (file, line number and the source line) and a
confidence level that explains how the call was resolved:

    high    resolved through an import, ``self.method`` or a same-file name
    medium  the name matches exactly one function in the whole repository
    low     ambiguous (several candidates) or a very common method name

Known limits (reported to the UI in ``limitations``):
    * Only Python is parsed (same as the rest of RepoLens).
    * Module-level code in files that also define functions is not part of
      the index, so callers living there are invisible.
    * Dynamic dispatch, getattr(), decorators that register callbacks and
      calls through variables cannot be resolved statically.
"""

from __future__ import annotations

import ast
import builtins
import os
import re
from collections import defaultdict, deque
from typing import Any

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

MAX_DEPTH = 3                 # how far "indirect" impact is followed
MAX_GRAPH_NODES = 60          # nodes sent to the interactive graph
MAX_AMBIGUOUS_TARGETS = 5     # ignore a name that matches more than this
MAX_EVIDENCE_PER_EDGE = 5
MAX_EXTERNAL_CALLS = 20

# Method names so common in the stdlib that a unique repo match is suspect.
COMMON_METHOD_NAMES = {
    "add", "append", "clear", "close", "copy", "count", "decode", "encode",
    "endswith", "extend", "find", "format", "get", "index", "insert", "items",
    "join", "keys", "lower", "open", "pop", "read", "readline", "remove",
    "replace", "run", "save", "send", "set", "sort", "split", "start",
    "startswith", "strip", "update", "upper", "values", "write",
}

FUNCTION_TYPES = {"function"}


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def make_id(record: dict) -> str:
    return f"{record['file_path']}:{record['start_line']}:{record['symbol']}"


def short_name(symbol: str) -> str:
    return symbol.rsplit(".", 1)[-1]


def display_name(record: dict) -> str:
    """Human label; script-style code is named after its file."""
    if record["symbol"] == "<module>":
        return f"{record['file_path'].rsplit('/', 1)[-1]} (script code)"
    return short_name(record["symbol"])


def is_test_file(file_path: str) -> bool:
    parts = file_path.lower().split("/")
    name = parts[-1]
    if any(p in ("tests", "test", "__tests__") for p in parts[:-1]):
        return True
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def is_test_function(record: dict) -> bool:
    """A real test: lives in a test file and is named like one."""
    if not is_test_file(record["file_path"]):
        return False
    if record["symbol"] == "<module>":
        return True  # script-style test files run at import time
    return short_name(record["symbol"]).lower().startswith("test")


def _module_candidates(module_path: str) -> list[str]:
    return [f"{module_path}.py", f"{module_path}/__init__.py"]


# --------------------------------------------------------------------------
# Import extraction (per file)
# --------------------------------------------------------------------------

def extract_imports(content: str) -> dict[str, dict]:
    """
    Map local names to what they import.

    Returns {local_name: {"module": "a.b" | relative-with-dots,
                          "level": int, "name": str | None}}
    ``name`` is None for ``import x`` (the local name is a module).
    """
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError, TypeError):
        return {}

    imports: dict[str, dict] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                module = alias.name if alias.asname else alias.name.split(".")[0]
                imports[local] = {"module": module, "level": 0, "name": None}
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                imports[alias.asname or alias.name] = {
                    "module": node.module or "",
                    "level": node.level or 0,
                    "name": alias.name,
                }
    return imports


# --------------------------------------------------------------------------
# Per-record call extraction
# --------------------------------------------------------------------------

class _RefCollector(ast.NodeVisitor):
    """
    Collects calls and non-call references inside ONE function body.
    Nested functions/classes are skipped: they have their own records.
    """

    def __init__(self):
        self.items: list[dict] = []
        self._call_funcs: set[int] = set()

    # nested definitions are separate records
    def visit_FunctionDef(self, node):
        pass

    def visit_AsyncFunctionDef(self, node):
        pass

    def visit_ClassDef(self, node):
        pass

    def visit_Call(self, node):
        self._call_funcs.add(id(node.func))
        item = self._describe(node.func, "call", node.lineno)
        if item:
            self.items.append(item)
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and id(node) not in self._call_funcs:
            self.items.append(
                {"kind": "reference", "name": node.id, "base": None,
                 "line": node.lineno}
            )

    def visit_Attribute(self, node):
        if isinstance(node.ctx, ast.Load) and id(node) not in self._call_funcs:
            base = node.value.id if isinstance(node.value, ast.Name) else None
            self.items.append(
                {"kind": "reference", "name": node.attr, "base": base,
                 "line": node.lineno}
            )
        self.generic_visit(node)

    @staticmethod
    def _describe(func, kind, line):
        if isinstance(func, ast.Name):
            return {"kind": kind, "name": func.id, "base": None, "line": line}
        if isinstance(func, ast.Attribute):
            base = None
            if isinstance(func.value, ast.Name):
                base = func.value.id
            elif (
                isinstance(func.value, ast.Call)
                and isinstance(func.value.func, ast.Name)
                and func.value.func.id == "super"
            ):
                base = "super()"
            return {"kind": kind, "name": func.attr, "base": base, "line": line}
        return None


def extract_references(record: dict) -> list[dict]:
    """
    Return calls/references made by a record, with ABSOLUTE line numbers.
    Falls back to a regex scan (low confidence) if the code cannot be parsed
    on its own, e.g. a 100-line chunk cut in the middle of a statement.
    """
    code = record.get("code", "")
    start = record.get("start_line", 1)

    tree = None
    offset = 0
    for source, shift in ((code, 0), ("if 1:\n" + code, 1)):
        try:
            tree = ast.parse(source)
            offset = shift
            break
        except (SyntaxError, ValueError, TypeError):
            continue

    if tree is None:
        return _regex_fallback(record)

    collector = _RefCollector()

    if record["type"] == "module":
        for stmt in tree.body:
            collector.visit(stmt)
    else:
        # tree.body[0] is the (possibly decorated) def, or the `if 1:` wrapper
        top = tree.body[0] if tree.body else None
        if offset and isinstance(top, ast.If):
            top = top.body[0] if top.body else None
        if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # default values are evaluated by the caller, so they are skipped
            for stmt in top.body:
                collector.visit(stmt)
        else:
            return []

    for item in collector.items:
        item["line"] = start + item["line"] - 1 - offset
    return collector.items


_CALL_RE = re.compile(r"(?:(\w+)\.)?\b([A-Za-z_]\w*)\s*\(")
_KEYWORDS = {"if", "for", "while", "with", "print", "return", "def", "class",
             "assert", "elif", "not", "and", "or", "in", "lambda", "except"}


def _regex_fallback(record: dict) -> list[dict]:
    items = []
    start = record.get("start_line", 1)
    for i, line in enumerate(record.get("code", "").splitlines()):
        stripped = line.split("#", 1)[0]
        for m in _CALL_RE.finditer(stripped):
            if m.group(2) in _KEYWORDS:
                continue
            items.append({
                "kind": "call", "name": m.group(2), "base": m.group(1),
                "line": start + i, "fallback": True,
            })
    return items


# --------------------------------------------------------------------------
# Call graph
# --------------------------------------------------------------------------

class CallGraph:
    def __init__(self, records: list[dict], file_imports: dict | None = None):
        # Work on copies: the shared index records must stay untouched.
        self.records = [
            dict(r, _id=make_id(r))
            for r in records
            if r.get("language", "python") == "python"
        ]
        self.file_imports = file_imports or {}

        self.nodes: dict[str, dict] = {}            # id -> record
        self.by_file_symbol: dict[tuple, list[dict]] = defaultdict(list)
        self.by_short: dict[str, list[dict]] = defaultdict(list)
        self.files: set[str] = set()
        self.has_module_records = False

        for r in self.records:
            self.nodes[r["_id"]] = r
            self.files.add(r["file_path"])
            self.by_file_symbol[(r["file_path"], r["symbol"])].append(r)
            if r["type"] in FUNCTION_TYPES:
                self.by_short[short_name(r["symbol"])].append(r)

        # edges[source_id][target_id] -> edge dict
        self.out_edges: dict[str, dict[str, dict]] = defaultdict(dict)
        self.in_edges: dict[str, dict[str, dict]] = defaultdict(dict)
        self.external_calls: dict[str, set[str]] = defaultdict(set)

        self._module_cache: dict[tuple, str | None] = {}
        self._build()

    # -- module / import resolution ---------------------------------------

    def _resolve_module_file(self, current_file: str, module: str, level: int) -> str | None:
        key = (current_file, module, level)
        if key in self._module_cache:
            return self._module_cache[key]

        result = None
        parts = [p for p in module.split(".") if p]

        if level > 0:
            base = current_file.split("/")[:-1]
            if level > 1:
                base = base[: len(base) - (level - 1)] if len(base) >= level - 1 else []
            path = "/".join(base + parts)
            for cand in _module_candidates(path) if parts else [
                "/".join(base + ["__init__.py"])
            ]:
                if cand in self.files:
                    result = cand
                    break
        elif parts:
            joined = "/".join(parts)
            matches = []
            for cand in _module_candidates(joined):
                for f in self.files:
                    if f == cand or f.endswith("/" + cand):
                        matches.append(f)
            if matches:
                # prefer the match sharing the longest directory prefix
                cur_dir = current_file.split("/")
                matches.sort(
                    key=lambda f: -len(os.path.commonprefix([f.split("/"), cur_dir]))
                )
                result = matches[0]

        self._module_cache[key] = result
        return result

    def _lookup(self, file_path: str, symbol: str) -> dict | None:
        found = self.by_file_symbol.get((file_path, symbol), [])
        found = [r for r in found if r["type"] in ("function", "class")]
        return found[0] if len(found) == 1 else None

    def _constructor_or_self(self, record: dict) -> dict | None:
        """Calling a class runs its __init__."""
        if record["type"] != "class":
            return record
        return self._lookup(record["file_path"], record["symbol"] + ".__init__")

    def _resolve_import_name(self, caller_file: str, local: str):
        info = self.file_imports.get(caller_file, {}).get(local)
        if not info:
            return None
        module_file = self._resolve_module_file(caller_file, info["module"], info["level"])
        if info["name"] is None:
            return ("module", module_file)
        # `from pkg import mod` -> mod may itself be a module file
        sub = ".".join(p for p in (info["module"], info["name"]) if p)
        sub_file = self._resolve_module_file(caller_file, sub, info["level"])
        if sub_file and (not module_file or not self._lookup(module_file, info["name"])):
            return ("module", sub_file)
        if module_file:
            rec = self._lookup(module_file, info["name"])
            if rec:
                return ("symbol", rec)
        return ("external", info["module"])

    # -- resolution of one reference ---------------------------------------

    def _resolve(self, caller: dict, ref: dict) -> list[tuple[dict, str, str]]:
        """Return [(target_record, confidence, how)]."""
        results = self._resolve_raw(caller, ref)
        if ref["kind"] == "reference":
            # A bare name or attribute that is not called is only trusted when
            # it was resolved exactly; otherwise variables would be mistaken
            # for functions.
            results = [r for r in results if r[1] == "high"]
        return results

    def _resolve_raw(self, caller: dict, ref: dict) -> list[tuple[dict, str, str]]:
        name, base = ref["name"], ref["base"]
        file_path = caller["file_path"]
        fallback = ref.get("fallback", False)
        caller_sym = caller["symbol"]

        def conf(c):  # regex fallback can never be better than "low"
            return "low" if fallback else c

        # --- bare name:  foo() / reference foo
        if base is None:
            # 1. nested functions in enclosing function scopes
            scope = caller_sym
            while scope and scope != "<module>":
                rec = self._lookup(file_path, f"{scope}.{name}")
                if rec and rec["type"] == "function" and self._is_function_scope(file_path, scope):
                    return [(rec, conf("high"), "defined inside the calling function")]
                scope = scope.rsplit(".", 1)[0] if "." in scope else ""
            # 2. imported name
            imp = self._resolve_import_name(file_path, name)
            if imp and imp[0] == "symbol":
                tgt = self._constructor_or_self(imp[1])
                if tgt:
                    return [(tgt, conf("high"), "resolved through an import")]
                return []
            if imp and imp[0] in ("external", "module"):
                return []
            # 3. module-level definition in the same file
            rec = self._lookup(file_path, name)
            if rec:
                tgt = self._constructor_or_self(rec)
                if tgt:
                    return [(tgt, conf("high"), "defined in the same file")]
                return []
            # 4. unique function elsewhere in the repo
            cands = [r for r in self.by_short.get(name, []) if "." not in r["symbol"]]
            if len(cands) == 1:
                return [(cands[0], conf("medium"), "only function with this name in the repository")]
            return []

        # --- attribute:  base.name
        if base in ("self", "cls") and "." in caller_sym:
            cls = caller_sym.rsplit(".", 1)[0]
            # nested functions inside methods: walk up to the class
            while cls and not self._is_class(file_path, cls) and "." in cls:
                cls = cls.rsplit(".", 1)[0]
            rec = self._lookup(file_path, f"{cls}.{name}")
            if rec and rec["type"] == "function":
                return [(rec, conf("high"), f"method of the same class ({cls})")]
            cands = [r for r in self.by_short.get(name, []) if "." in r["symbol"]]
            if len(cands) == 1:
                return [(cands[0], conf("medium"), "inherited/other method with this unique name")]
            return []

        if base == "super()":
            cands = [r for r in self.by_short.get(name, []) if "." in r["symbol"]
                     and r["file_path"] != "" and r["symbol"] != caller_sym]
            if len(cands) == 1:
                return [(cands[0], conf("medium"), "super() call to a unique method")]
            return []

        if base:
            imp = self._resolve_import_name(file_path, base)
            if imp and imp[0] == "module" and imp[1]:
                rec = self._lookup(imp[1], name)
                if rec:
                    tgt = self._constructor_or_self(rec)
                    if tgt:
                        return [(tgt, conf("high"), f"called through imported module '{base}'")]
                return []
            if imp and imp[0] == "external":
                return []
            if imp and imp[0] == "symbol" and imp[1]["type"] == "class":
                rec = self._lookup(imp[1]["file_path"], f"{imp[1]['symbol']}.{name}")
                if rec:
                    return [(rec, conf("high"), f"method of imported class '{base}'")]
                return []

        # 5. unknown receiver: object.method()
        cands = [r for r in self.by_short.get(name, []) if r["symbol"] != caller_sym or "." in r["symbol"]]
        if not cands:
            return []
        if len(cands) > MAX_AMBIGUOUS_TARGETS:
            return []
        if len(cands) == 1:
            level = "low" if name in COMMON_METHOD_NAMES else "medium"
            return [(cands[0], conf(level), f"only function named '{name}' in the repository")]
        return [(c, "low", f"one of {len(cands)} functions named '{name}'") for c in cands]

    def _is_class(self, file_path, symbol) -> bool:
        return any(r["type"] == "class" for r in self.by_file_symbol.get((file_path, symbol), []))

    def _is_function_scope(self, file_path, symbol) -> bool:
        return any(r["type"] == "function" for r in self.by_file_symbol.get((file_path, symbol), []))

    # -- build ----------------------------------------------------------------

    def _build(self):
        for caller in self.records:
            if caller["type"] == "class":
                continue
            if caller["type"] == "module":
                self.has_module_records = True

            for ref in extract_references(caller):
                targets = self._resolve(caller, ref)
                if not targets:
                    if ref["kind"] == "call":
                        self._note_external(caller, ref)
                    continue
                for target, confidence, how in targets:
                    if target["_id"] == caller["_id"] and ref["kind"] == "reference":
                        continue
                    self._add_edge(caller, target, ref, confidence, how)

    def _note_external(self, caller, ref):
        """Remember calls to libraries (not builtins, not unknown objects)."""
        base, name = ref["base"], ref["name"]
        imports = self.file_imports.get(caller["file_path"], {})
        if base is None:
            if name in imports and not hasattr(builtins, name):
                self.external_calls[caller["_id"]].add(name)
        elif base in imports:
            self.external_calls[caller["_id"]].add(f"{base}.{name}")

    def _add_edge(self, caller, target, ref, confidence, how):
        cid, tid = caller["_id"], target["_id"]
        edge = self.out_edges[cid].get(tid)
        if edge is None:
            edge = {
                "source": cid, "target": tid, "kind": ref["kind"],
                "confidence": confidence, "how": how, "evidence": [],
            }
            self.out_edges[cid][tid] = edge
            self.in_edges[tid][cid] = edge
        else:
            # keep the strongest information
            if ref["kind"] == "call":
                edge["kind"] = "call"
            order = {"low": 0, "medium": 1, "high": 2}
            if order[confidence] > order[edge["confidence"]]:
                edge["confidence"], edge["how"] = confidence, how

        if len(edge["evidence"]) < MAX_EVIDENCE_PER_EDGE:
            line = ref["line"]
            if not any(e["line"] == line for e in edge["evidence"]):
                edge["evidence"].append({
                    "file_path": caller["file_path"],
                    "line": line,
                    "text": _source_line(caller, line),
                    "kind": ref["kind"],
                })

    # -- queries --------------------------------------------------------------

    def list_functions(self, query: str = "", limit: int = 50) -> list[dict]:
        q = query.strip().lower()
        out = []
        for r in self.records:
            if r["type"] != "function":
                continue
            if q and q not in r["symbol"].lower() and q not in r["file_path"].lower():
                continue
            out.append(self._summary(r))
        # exact / prefix matches first, then busiest functions
        def rank(item):
            s = item["symbol"].lower()
            return (
                0 if q and (s == q or short_name(s) == q) else
                1 if q and short_name(s).startswith(q) else 2,
                -(item["caller_count"] + item["dependency_count"]),
                item["symbol"],
            )
        out.sort(key=rank)
        return out[:limit]

    def find(self, file_path: str, symbol: str, start_line: int | None = None) -> dict | None:
        found = [r for r in self.by_file_symbol.get((file_path, symbol), [])
                 if r["type"] in ("function", "module")]
        if start_line is not None:
            exact = [r for r in found if r["start_line"] == start_line]
            found = exact or found
        return found[0] if found else None

    def _summary(self, r: dict) -> dict:
        return {
            "id": r["_id"],
            "symbol": r["symbol"],
            "label": display_name(r),
            "file_path": r["file_path"],
            "start_line": r["start_line"],
            "end_line": r["end_line"],
            "type": r["type"],
            "is_test": is_test_file(r["file_path"]),
            "caller_count": len(self.in_edges.get(r["_id"], {})),
            "dependency_count": len(self.out_edges.get(r["_id"], {})),
        }

    def stats(self) -> dict:
        return {
            "functions": sum(1 for r in self.records if r["type"] == "function"),
            "edges": sum(len(v) for v in self.out_edges.values()),
            "files": len(self.files),
        }

    # -- the actual analysis -----------------------------------------------

    def analyze(self, target: dict) -> dict:
        tid = target["_id"]

        direct_in = self.in_edges.get(tid, {})
        direct_out = self.out_edges.get(tid, {})

        # --- reverse BFS: who is affected, directly and transitively
        depth_of: dict[str, int] = {tid: 0}
        parent: dict[str, str] = {}
        queue = deque([tid])
        while queue:
            cur = queue.popleft()
            d = depth_of[cur]
            if d >= MAX_DEPTH:
                continue
            for caller_id in self.in_edges.get(cur, {}):
                if caller_id in depth_of:
                    continue
                depth_of[caller_id] = d + 1
                parent[caller_id] = cur
                # tests are leaves of the impact chain
                if not is_test_file(self.nodes[caller_id]["file_path"]):
                    queue.append(caller_id)

        def chain(node_id):
            path, cur = [], node_id
            while cur != tid:
                path.append(display_name(self.nodes[cur]))
                cur = parent[cur]
            path.append(display_name(target))
            return path  # from the node back to the target

        direct_callers, indirect_callers = [], []
        direct_tests, indirect_tests = [], []

        for nid, depth in sorted(depth_of.items(), key=lambda kv: (kv[1], kv[0])):
            if nid == tid:
                continue
            rec = self.nodes[nid]
            testy = is_test_file(rec["file_path"])
            item = self._summary(rec)
            item["depth"] = depth
            item["path"] = chain(nid) if depth > 1 else None

            if depth == 1:
                edge = direct_in[nid]
                item.update(self._edge_fields(edge, "calls" if edge["kind"] == "call" else "references", target))
                item["is_test_function"] = is_test_function(rec)
                (direct_tests if testy else direct_callers).append(item)
            else:
                item["reason"] = (
                    f"Reaches `{display_name(target)}` indirectly "
                    f"({depth} hops): " + " → ".join(chain(nid))
                )
                item["confidence"] = self._path_confidence(nid, parent, tid)
                item["evidence"] = []
                item["is_test_function"] = is_test_function(rec)
                (indirect_tests if testy else indirect_callers).append(item)

        dependencies = []
        for did, edge in direct_out.items():
            rec = self.nodes[did]
            item = self._summary(rec)
            item.update(self._edge_fields(edge, "called by" if edge["kind"] == "call" else "referenced by", target, outgoing=True))
            item["depth"] = 1
            dependencies.append(item)
        dependencies.sort(key=lambda d: (d["evidence"][0]["line"] if d["evidence"] else 0))

        external = sorted(self.external_calls.get(tid, set()))[:MAX_EXTERNAL_CALLS]

        # --- affected files
        files: dict[str, dict] = {}
        for bucket, role in (
            (direct_callers, "caller"), (indirect_callers, "indirect caller"),
            (direct_tests, "test"), (indirect_tests, "indirect test"),
        ):
            for it in bucket:
                f = files.setdefault(it["file_path"], {"file_path": it["file_path"], "roles": set(), "functions": []})
                f["roles"].add(role)
                f["functions"].append(it["label"])
        f = files.setdefault(target["file_path"], {"file_path": target["file_path"], "roles": set(), "functions": []})
        f["roles"].add("selected function")
        f["functions"].append(display_name(target))
        affected_files = [
            {"file_path": v["file_path"], "roles": sorted(v["roles"]),
             "functions": sorted(set(v["functions"]))}
            for v in files.values()
        ]
        affected_files.sort(key=lambda x: (x["file_path"] != target["file_path"], x["file_path"]))

        summary = self._summarize(
            target, direct_callers, indirect_callers, direct_tests,
            indirect_tests, dependencies, affected_files,
        )

        graph = self._graph(
            target, direct_callers, indirect_callers, direct_tests,
            indirect_tests, dependencies,
        )

        return {
            "target": {
                **self._summary(target),
                "code": target.get("code", ""),
            },
            "direct_callers": direct_callers,
            "indirect_callers": indirect_callers,
            "dependencies": dependencies,
            "related_tests": direct_tests,
            "indirect_tests": indirect_tests,
            "external_calls": external,
            "affected_files": affected_files,
            "summary": summary,
            "graph": graph,
            "limitations": self._limitations(),
        }

    # -- analysis helpers --------------------------------------------------

    def _edge_fields(self, edge, verb, target, outgoing=False):
        caller = self.nodes[edge["source"]]
        callee = self.nodes[edge["target"]]
        first = edge["evidence"][0] if edge["evidence"] else None
        where = f"{first['file_path']}:{first['line']}" if first else ""
        if outgoing:
            reason = (
                f"`{display_name(target)}` {'calls' if edge['kind'] == 'call' else 'references'} "
                f"`{display_name(callee)}` at {where} ({edge['how']})."
            )
        else:
            reason = (
                f"`{display_name(caller)}` {'calls' if edge['kind'] == 'call' else 'references'} "
                f"`{display_name(target)}` at {where} ({edge['how']})."
            )
        return {
            "relationship": edge["kind"],
            "confidence": edge["confidence"],
            "reason": reason,
            "evidence": edge["evidence"],
        }

    def _path_confidence(self, nid, parent, tid):
        order = {"low": 0, "medium": 1, "high": 2}
        worst, cur = "high", nid
        while cur != tid:
            nxt = parent[cur]
            c = self.out_edges[cur][nxt]["confidence"]
            if order[c] < order[worst]:
                worst = c
            cur = nxt
        return worst

    def _summarize(self, target, dc, ic, dt, it, deps, files):
        other_files = [f for f in files if f["file_path"] != target["file_path"]]
        high_conf_callers = [c for c in dc if c["confidence"] != "low"]

        # Transparent, deterministic score: trusted callers weigh more than
        # low-confidence ones; reach across files and hops adds to it.
        score = (
            8 * len(high_conf_callers)
            + 4 * len(dc)
            + 4 * len(ic)
            + 6 * len(other_files)
        )
        factors = []
        if dc:
            factors.append(f"{len(dc)} direct caller(s)")
        if ic:
            factors.append(f"{len(ic)} indirect caller(s) within {MAX_DEPTH} hops")
        if other_files:
            factors.append(f"used from {len(other_files)} other file(s)")

        if dt:
            score -= min(len(dt), 3) * 8
            factors.append(f"{len(dt)} test(s) reference it directly (lowers risk)")
        elif it:
            score -= 4
            factors.append(f"covered only indirectly by {len(it)} test(s)")
        elif dc or ic:
            score += 10
            factors.append("no tests found for it or its callers (raises risk)")

        score = max(0, min(100, score))
        risk = "high" if score >= 50 else "medium" if score >= 20 else "low"

        name = f"`{display_name(target)}`"
        if not dc and not ic and not dt and not it:
            headline = (
                f"No callers or tests were found for {name}. Changing it looks "
                "isolated, but dynamic calls or module-level code may not be visible."
            )
        else:
            headline = (
                f"Changing {name} may affect {len(dc)} direct caller(s), "
                f"{len(ic)} indirect caller(s), {len(other_files) + 1} file(s) "
                f"and {len(dt)} directly related test(s)."
            )

        bullets = []
        if dc:
            names = ", ".join(f"`{c['label']}`" for c in dc[:4])
            more = f" and {len(dc) - 4} more" if len(dc) > 4 else ""
            bullets.append(f"Direct callers to re-check: {names}{more}.")
        if deps:
            bullets.append(
                f"It depends on {len(deps)} repository function(s); "
                "changes to its inputs or return value should stay compatible."
            )
        if dt:
            bullets.append("Run these tests first: " + ", ".join(f"`{t['label']}`" for t in dt[:4]) + ".")
        elif it:
            bullets.append("No direct tests, but these reach it indirectly: " + ", ".join(f"`{t['label']}`" for t in it[:4]) + ".")
        else:
            bullets.append("No tests reference this function; consider adding one before changing it.")
        if any(c["confidence"] == "low" for c in dc + dt):
            bullets.append("Some links are low-confidence (ambiguous names). Verify them in the evidence.")

        return {
            "risk": risk,
            "score": score,
            "headline": headline,
            "bullets": bullets,
            "factors": factors,
            "counts": {
                "direct_callers": len(dc),
                "indirect_callers": len(ic),
                "dependencies": len(deps),
                "direct_tests": len(dt),
                "indirect_tests": len(it),
                "files": len(files),
            },
        }

    def _graph(self, target, dc, ic, dt, it, deps):
        kind_of = []
        kind_of += [(c, "caller") for c in dc]
        kind_of += [(c, "dependency") for c in deps]
        kind_of += [(c, "test") for c in dt]
        kind_of += [(c, "indirect") for c in ic]
        kind_of += [(c, "indirect_test") for c in it]

        nodes = {
            target["_id"]: {
                "id": target["_id"], "label": display_name(target),
                "symbol": target["symbol"], "file_path": target["file_path"],
                "start_line": target["start_line"], "kind": "target",
            }
        }
        for item, kind in kind_of:
            if len(nodes) >= MAX_GRAPH_NODES:
                break
            if item["id"] in nodes:
                # a function can be both caller and dependency (recursion/cycle)
                nodes[item["id"]]["also"] = kind
                continue
            nodes[item["id"]] = {
                "id": item["id"], "label": item["label"],
                "symbol": item["symbol"], "file_path": item["file_path"],
                "start_line": item["start_line"], "kind": kind,
                "confidence": item.get("confidence"),
            }

        edges = []
        for sid, targets in self.out_edges.items():
            if sid not in nodes:
                continue
            for tid2, e in targets.items():
                if tid2 in nodes:
                    edges.append({
                        "source": sid, "target": tid2,
                        "kind": e["kind"], "confidence": e["confidence"],
                    })
        return {
            "nodes": list(nodes.values()),
            "edges": edges,
            "truncated": len(kind_of) + 1 > MAX_GRAPH_NODES,
        }

    def _limitations(self) -> list[str]:
        notes = [
            "Only Python files are analyzed.",
            "Calls made from module-level code of files that also define "
            "functions are not indexed, so those callers are not shown.",
            "Dynamic calls (getattr, callbacks stored in variables, "
            "decorator registration) cannot be resolved statically.",
        ]
        if self.has_module_records:
            notes.append(
                "Script-style files without functions are analyzed in 100-line "
                "chunks; chunks that cannot be parsed use a low-confidence scan."
            )
        return notes


def _source_line(record: dict, line: int) -> str:
    idx = line - record["start_line"]
    lines = record.get("code", "").splitlines()
    if 0 <= idx < len(lines):
        return lines[idx].strip()
    return ""


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def build_call_graph(records: list[dict], file_imports: dict | None = None) -> CallGraph:
    return CallGraph(records, file_imports)
