"""Host-side helper for invoking an adapter executable (DESIGN.md §2, DL-0007).

The adapter itself is just an executable satisfying the verb protocol — this module is
the runner-side half of that contract: it builds the argv/environment for each call and
returns a small result object. It does not know anything KiCad-specific; that lives in
`runner/adapters/kicad.py`.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AdapterResult:
    returncode: int
    stdout: str
    stderr: str


DEFAULT_ADAPTER = Path(__file__).resolve().parent / "adapters" / "kicad.py"


def _argv(adapter_path: Path) -> list[str]:
    """A `.py` adapter is run with the current interpreter; anything else is assumed to
    be directly executable (a shell script, a compiled binary, ...) — this is what lets
    `--adapter` point at a non-Python implementation-under-test without special-casing."""
    import sys

    if adapter_path.suffix == ".py":
        return [sys.executable, str(adapter_path)]
    return [str(adapter_path)]


class Adapter:
    def __init__(self, adapter_path: Path | None = None):
        self.path = Path(adapter_path) if adapter_path else DEFAULT_ADAPTER
        self._capabilities: set[str] | None = None

    def _run(self, args: list[str]) -> AdapterResult:
        env = dict(os.environ)
        # DESIGN §4: environment pinning happens at the runner, not the adapter, so it
        # applies uniformly no matter which adapter is under test.
        env["LC_ALL"] = "C.UTF-8"
        env["TZ"] = "UTC"
        proc = subprocess.run(
            [*_argv(self.path), *args],
            capture_output=True,
            text=True,
            env=env,
        )
        return AdapterResult(proc.returncode, proc.stdout, proc.stderr)

    def capabilities(self) -> set[str]:
        if self._capabilities is None:
            result = self._run(["capabilities"])
            if result.returncode == 0:
                try:
                    self._capabilities = set(json.loads(result.stdout))
                except (json.JSONDecodeError, TypeError):
                    self._capabilities = set()
            else:
                # An adapter that doesn't answer `capabilities` is assumed to support
                # everything it's asked for (fail open on capability negotiation itself,
                # never on the checks it's actually judged by).
                self._capabilities = None
        return self._capabilities

    def supports(self, verb: str) -> bool:
        caps = self.capabilities()
        return True if caps is None else verb in caps

    def version(self) -> str:
        result = self._run(["version"])
        return result.stdout.strip() if result.returncode == 0 else "unknown"

    def identity(self) -> str:
        """`version --format about` -- the fuller oracle-identity record (DL-0010:
        "Record kicad-cli version --format about in every run")."""
        result = self._run(["version", "--format", "about"])
        return result.stdout.strip() if result.returncode == 0 else "unknown"

    def invoke(
        self,
        verb: str,
        inputs: list[Path],
        out_dir: Path,
        root: str | None = None,
        fmt: str | None = None,
        extra_args: list[str] | None = None,
    ) -> AdapterResult:
        args = [verb]
        for p in inputs:
            args += ["--in", str(p)]
        args += ["--out", str(out_dir)]
        if root:
            args += ["--root", root]
        if fmt:
            args += ["--format", fmt]
        if extra_args:
            args += list(extra_args)
        return self._run(args)
