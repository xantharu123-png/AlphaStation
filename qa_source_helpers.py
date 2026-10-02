"""Source-contract helpers independent of transformed offline code line numbers."""
import ast
from pathlib import Path


def function_source(module, name):
    """Read one real top-level function by AST boundaries, not inspect offsets.

    Offline QA transforms production code before compilation. Its line numbers
    need not match the saved file, while the source contract must inspect the
    named production function rather than a neighboring helper.
    """
    source = Path(module.__file__).read_text(encoding="utf-8-sig")
    node = next((item for item in ast.parse(source).body
                 if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and item.name == name), None)
    if node is None:
        raise AssertionError(f"Missing source function: {name}")
    return ast.get_source_segment(source, node)
