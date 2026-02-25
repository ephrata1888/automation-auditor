from __future__ import annotations

import ast
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Set


logger = logging.getLogger(__name__)


def clone_repo_sandboxed(repo_path: str) -> Path:
    """Clone a git repository into a sandboxed temporary directory.

    This uses ``tempfile.mkdtemp`` to create an isolated directory under the
    system temporary folder and clones the repository into a ``repo`` subfolder.
    The returned path is the directory containing the cloned repository.

    Note:
        The caller is responsible for any long-term cleanup of the temporary
        directory, although most operating systems periodically clean temp
        locations. The key safety property is that cloning never touches
        arbitrary locations on the filesystem.
    """
    tmpdir = Path(tempfile.mkdtemp())
    clone_target = tmpdir / "repo"

    try:
        clone_proc = subprocess.run(
            ["git", "clone", "--depth", "100", repo_path, str(clone_target)],
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, ValueError) as exc:
        logger.debug("Failed to invoke git clone: %s", exc)
        raise RuntimeError(f"Failed to execute git clone: {exc}") from exc

    if clone_proc.returncode != 0:
        logger.debug(
            "git clone failed with code %s: %s",
            clone_proc.returncode,
            clone_proc.stderr,
        )
        message = clone_proc.stderr.strip() or clone_proc.stdout.strip() or (
            "Unknown git clone error"
        )
        raise RuntimeError(f"git clone failed: {message}")

    return clone_target


def extract_git_log(clone_path: Path) -> str:
    """Run ``git log --oneline --reverse`` in the cloned repository."""
    try:
        log_proc = subprocess.run(
            [
                "git",
                "log",
                "--oneline",
                "--reverse",
                "--date=iso",
                "--pretty=format:%h %ad %s",
            ],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(clone_path),
        )
    except (OSError, ValueError) as exc:
        logger.debug("Failed to invoke git log: %s", exc)
        raise RuntimeError(f"Failed to execute git log: {exc}") from exc

    if log_proc.returncode != 0:
        logger.debug(
            "git log failed with code %s: %s",
            log_proc.returncode,
            log_proc.stderr,
        )
        message = log_proc.stderr.strip() or log_proc.stdout.strip() or (
            "Unknown git log error"
        )
        raise RuntimeError(f"git log failed: {message}")

    return log_proc.stdout.strip()


def _safe_read_file(file_path: Path) -> Optional[str]:
    """Safely read a text file, returning None on failure."""
    try:
        if not file_path.is_file():
            logger.debug("File not found: %s", file_path)
            return None
        return file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug("Failed to read file %s: %s", file_path, exc)
        return None


def parse_file_ast(file_path: Path) -> Optional[ast.AST]:
    """Read and parse a Python file into an AST, handling errors safely."""
    source = _safe_read_file(file_path)
    if source is None:
        return None
    try:
        return ast.parse(source, filename=str(file_path))
    except SyntaxError as exc:
        logger.debug("Failed to parse AST for %s: %s", file_path, exc)
        return None


def _extract_snippet(
    source: str,
    start_line: Optional[int],
    end_line: Optional[int],
) -> Optional[str]:
    """Extract a code snippet from the given source by line range."""
    if start_line is None:
        return None

    lines = source.splitlines()
    # Convert 1-based AST line numbers to 0-based indices.
    start_idx = max(start_line - 1, 0)
    end_idx = (end_line - 1) if end_line is not None else start_idx
    end_idx = min(end_idx, len(lines) - 1)

    if start_idx > end_idx or start_idx >= len(lines):
        return None

    return "\n".join(lines[start_idx : end_idx + 1])


def find_call_snippet(
    module: ast.AST,
    source: str,
    func_names: Set[str],
) -> Optional[str]:
    """Find the first call to any function in ``func_names`` and return a snippet."""
    for node in ast.walk(module):
        if isinstance(node, ast.Call):
            func = node.func
            name: Optional[str] = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr

            if name in func_names:
                return _extract_snippet(
                    source,
                    getattr(node, "lineno", None),
                    getattr(node, "end_lineno", None),
                )
    return None


def find_method_call_snippet(
    module: ast.AST,
    source: str,
    receiver_name: str,
    method_name: str,
) -> Optional[str]:
    """Find the first call to ``receiver_name.method_name`` and return a snippet."""
    for node in ast.walk(module):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func
            if (
                isinstance(attr.value, ast.Name)
                and attr.value.id == receiver_name
                and attr.attr == method_name
            ):
                return _extract_snippet(
                    source,
                    getattr(node, "lineno", None),
                    getattr(node, "end_lineno", None),
                )
    return None


def find_symbol_usage_snippet(
    module: ast.AST,
    source: str,
    symbol: str,
) -> Optional[str]:
    """Find the first usage of a given symbol name and return a snippet."""
    # Prefer call sites first (e.g., wiring nodes into the graph).
    for node in ast.walk(module):
        if isinstance(node, ast.Call):
            # Direct calls like RepoInvestigator(...)
            if isinstance(node.func, ast.Name) and node.func.id == symbol:
                return _extract_snippet(
                    source,
                    getattr(node, "lineno", None),
                    getattr(node, "end_lineno", None),
                )
            # Attribute-based calls like nodes.RepoInvestigator(...)
            if isinstance(node.func, ast.Attribute) and node.func.attr == symbol:
                return _extract_snippet(
                    source,
                    getattr(node, "lineno", None),
                    getattr(node, "end_lineno", None),
                )

    # Fallback: any name usage.
    for node in ast.walk(module):
        if isinstance(node, ast.Name) and node.id == symbol:
            return _extract_snippet(
                source,
                getattr(node, "lineno", None),
                getattr(node, "end_lineno", None),
            )

    return None

