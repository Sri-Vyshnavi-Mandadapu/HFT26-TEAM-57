
import ast
from typing import Any


class RepositoryASTParser(ast.NodeVisitor):

    def __init__(self, content: str, file_path: str):

        self.lines = content.splitlines()
        self.file_path = file_path
        self.records = []
        self.scope = []

    def add_record(self, node, record_type):

        start_line = node.lineno
        end_line = node.end_lineno

        # Include decorators in the source range.
        decorators = getattr(node, "decorator_list", [])

        if decorators:
            start_line = min(
                start_line,
                *[decorator.lineno for decorator in decorators]
            )

        symbol = ".".join(
            self.scope + [node.name]
        )

        code = "\n".join(
            self.lines[start_line - 1:end_line]
        )

        self.records.append({

            "file_path": self.file_path,

            "symbol": symbol,

            "type": record_type,

            "start_line": start_line,

            "end_line": end_line,

            "code": code,

            "language": "python"
        })

    def visit_ClassDef(self, node):

        self.add_record(node, "class")

        self.scope.append(node.name)

        self.generic_visit(node)

        self.scope.pop()

    def visit_FunctionDef(self, node):

        self.add_record(node, "function")

        self.scope.append(node.name)

        self.generic_visit(node)

        self.scope.pop()

    def visit_AsyncFunctionDef(self, node):

        self.add_record(node, "function")

        self.scope.append(node.name)

        self.generic_visit(node)

        self.scope.pop()


def parse_python(
    content: str,
    file_path: str
) -> list[dict[str, Any]]:

    try:

        tree = ast.parse(
            content,
            filename=file_path
        )

    except (SyntaxError, ValueError, TypeError):

        return []

    parser = RepositoryASTParser(
        content,
        file_path
    )

    parser.visit(tree)

    records = parser.records

    # Preserve simple Python scripts and module-level code
    # when no function or class is found.
    if not records and content.strip():

        lines = content.splitlines()

        chunk_size = 100

        for start in range(0, len(lines), chunk_size):

            chunk = lines[start:start + chunk_size]

            if not "\n".join(chunk).strip():
                continue

            records.append({

                "file_path": file_path,

                "symbol": "<module>",

                "type": "module",

                "start_line": start + 1,

                "end_line": start + len(chunk),

                "code": "\n".join(chunk),

                "language": "python"
            })

    return sorted(
        records,
        key=lambda record: (
            record["start_line"],
            -record["end_line"]
        )
    )
