"""Keep docs/USAGE.md honest: every sample must refer to things that exist.

* Python samples must compile, and their imports must resolve.
* Calls like ``fo.cost.cost_by(...)`` must name a real operation group and method, and only use keyword arguments that
  the method accepts.
* Shell samples that call ``foundry-finops`` must parse with the real argument parser.
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import inspect
import io
import re
import shlex
import unittest
from pathlib import Path

from foundry_finops import FinOpsClient, StaticTokenCredential, cli

DOC = Path(__file__).resolve().parents[1] / "docs" / "USAGE.md"
_CLIENT = FinOpsClient(StaticTokenCredential("t"), "sub-1")
#: Operation groups: this package's (cached properties) and the Microsoft client's (set in its constructor).
GROUPS = {n for n in dir(_CLIENT) if not n.startswith("_") and type(getattr(_CLIENT, n)).__name__.endswith("Operations")}
#: Modules of helper functions that take the client first.
HELPER_MODULES = ("cost_controls", "resources")


def blocks(lang: str) -> list[tuple[int, str]]:
    text = DOC.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(rf"```{lang}\n(.*?)```", text, re.S):
        out.append((text[: m.start()].count("\n") + 1, m.group(1)))
    return out


class PythonSamples(unittest.TestCase):
    def test_there_are_samples(self):
        self.assertGreater(len(blocks("python")), 30)

    def test_compile_and_imports_resolve(self):
        for line, code in blocks("python"):
            with self.subTest(line=line):
                tree = ast.parse(code)  # syntax errors fail here
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        if node.module.split(".")[0] not in ("foundry_finops",):
                            continue  # azure, openai, requests: third-party, not ours to resolve
                        mod = importlib.import_module(node.module)
                        for alias in node.names:
                            self.assertTrue(
                                hasattr(mod, alias.name) or _is_submodule(node.module, alias.name),
                                f"line {line}: cannot import {alias.name} from {node.module}",
                            )

    def test_client_calls_use_real_methods_and_arguments(self):
        checked = 0
        for line, code in blocks("python"):
            for node in ast.walk(ast.parse(code)):
                if not isinstance(node, ast.Call):
                    continue
                chain = _attr_chain(node.func)
                if len(chain) == 2 and chain[0] in HELPER_MODULES:
                    module = importlib.import_module(f"foundry_finops.{chain[0]}")
                    with self.subTest(line=line, call=".".join(chain)):
                        fn = getattr(module, chain[1], None)
                        self.assertTrue(callable(fn), f"{chain[0]}.{chain[1]} does not exist")
                        params = inspect.signature(fn).parameters
                        for kw in node.keywords:
                            if kw.arg is not None:
                                self.assertIn(kw.arg, params, f"{chain[0]}.{chain[1]} has no argument {kw.arg!r}")
                        checked += 1
                    continue
                if len(chain) != 3 or chain[0] != "fo":
                    continue
                _, group, method = chain
                with self.subTest(line=line, call=".".join(chain)):
                    self.assertIn(group, GROUPS, f"{group} is not an operation group")
                    ops = _group_class(group)
                    fn = getattr(ops, method, None)
                    self.assertTrue(callable(fn), f"{group}.{method} does not exist")
                    params = inspect.signature(fn).parameters
                    takes_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
                    for kw in node.keywords:
                        if kw.arg is not None and not takes_kwargs:
                            self.assertIn(kw.arg, params, f"{group}.{method} has no argument {kw.arg!r}")
                    checked += 1
        self.assertGreater(checked, 100)


class ShellSamples(unittest.TestCase):
    def commands(self) -> list[tuple[int, list[str]]]:
        out = []
        for line, code in blocks("bash"):
            joined = re.sub(r"\\\n\s*", " ", code)
            for raw in joined.splitlines():
                raw = re.sub(r"<[^>]+>", "x", raw.split(" #")[0].strip())
                if not raw.startswith("foundry-finops"):
                    continue
                raw = re.sub(r"\s>\s\S+$", "", raw)  # drop an output redirect
                argv = shlex.split(raw)[1:]
                if argv and argv[0] != "--help":
                    out.append((line, argv))
        return out

    def test_every_cli_example_parses(self):
        parser = cli._build_parser()
        cmds = self.commands()
        self.assertGreater(len(cmds), 35)
        for line, argv in cmds:
            with self.subTest(line=line, argv=" ".join(argv)):
                err = io.StringIO()
                with contextlib.redirect_stderr(err), contextlib.suppress(SystemExit):
                    parser.parse_args(argv)
                self.assertNotIn("error:", err.getvalue())


def _is_submodule(package: str, name: str) -> bool:
    try:
        importlib.import_module(f"{package}.{name}")
    except ImportError:
        return False
    return True


def _attr_chain(node: ast.AST) -> list[str]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return list(reversed(parts))


def _group_class(group: str) -> type:
    return type(getattr(_CLIENT, group))


if __name__ == "__main__":
    unittest.main()
