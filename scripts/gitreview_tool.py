#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation
# pyright: reportAny=false, reportUnusedCallResult=false
# pyright: reportImplicitStringConcatenation=false, reportUnreachable=false
"""Create and rewrite .gitreview files across bare Gerrit repositories.

This tool runs inside a gerrit-action container (as the ``gerrit``
user) and walks every bare repo under ``/var/gerrit/git``.  It
supports two operations driven by sub-commands:

* ``create-missing`` synthesises a minimal ``.gitreview`` file for
  every repo whose default-branch tip lacks one.  ``host`` and
  ``port`` are sourced from the first usable ``.gitreview`` found
  on any repo's default-branch (HEAD) tip; all other keys come
  from the bare repo's on-disk path (``project``) and its ``HEAD``
  symbolic ref (``defaultbranch``).

* ``rewrite`` rewrites the ``host=`` line (and optionally the
  ``port=`` line) of every existing ``.gitreview`` blob on every
  ``refs/heads/*`` tip.  Tailscale callers pass ``--keep-port`` to
  leave the port line alone.

Both commands honour two commit strategies via ``--strategy``:

* ``new``   - append a synthetic ``Chore:`` commit on top of each
  branch tip.  The change is visible as its own commit in
  ``git log``.

* ``amend`` - rebuild the existing tip commit in place, preserving
  the original author, committer, dates, message and parents.  The
  ``.gitreview`` blob simply differs from upstream inside the
  rewritten tip commit; no ``Chore`` commit appears anywhere in
  ``git log``.

The script depends only on the Python 3 standard library and the
``git`` plumbing commands available inside the container.  It targets
Python 3.10 or newer (the gerrit-action container ships Python 3.11);
an explicit ``sys.version_info`` check at import time fails fast with
a clear message on older interpreters.

The tool tolerates the still-running replication of the test
deployment: a per-branch compare-and-swap ``update-ref`` is used,
and a single racing branch is warned about and counted as
``skipped`` rather than aborting the rest of the pass.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

# Fail fast with a readable message on Python < 3.10.  Most of the
# modern type syntax used below (PEP 604 unions ``X | None`` and
# built-in generics such as ``list[str]``) is confined to annotation
# context and survives via ``from __future__ import annotations`` on
# older interpreters, but ``argparse.add_mutually_exclusive_group``
# and the typeshed stubs we rely on are best-tested on 3.10+, and
# the gerrit-action container ships Python 3.11.  Failing here with
# a clear message is friendlier than an obscure runtime error later.
if sys.version_info < (3, 10):  # pragma: no cover - environment guard
    sys.stderr.write(
        f"gitreview_tool requires Python 3.10 or newer; got {sys.version.split()[0]}\n"
    )
    sys.exit(2)

# Bare repository root inside the Gerrit container.
GIT_ROOT = Path("/var/gerrit/git")

# Internal Gerrit repos that never carry a ``.gitreview`` and must
# never have one synthesised by ``create-missing``.  The ``rewrite``
# command skips them implicitly via the blob-presence check; the
# create command needs an explicit deny-list to avoid drifting into
# Gerrit's own management state.
INTERNAL_REPOS: frozenset[str] = frozenset(
    {
        "All-Projects.git",
        "All-Users.git",
        "All-External-IDs.git",
        "Sequences.git",
    }
)

# Default Gerrit SSH port to fall back to when the harvested template
# .gitreview carries a host= line but no port= line.
DEFAULT_GERRIT_SSH_PORT = "29418"

# Line matchers for the host= and port= entries in a .gitreview.
# Leading whitespace is tolerated; key name is lowercase; whitespace
# around the '=' is permitted.  Matches the awk pattern from the
# previous shell implementation so byte-exact no-op rewrites still
# behave identically.
HOST_LINE_RE = re.compile(rb"^[ \t]*host[ \t]*=")
PORT_LINE_RE = re.compile(rb"^[ \t]*port[ \t]*=")

# Matches a syntactically safe host literal: ASCII letters, digits,
# '.', '-', ':', '/' (the last two so IPv6 literals and tunnel-style
# 'host.example.com' all pass).  Crucially excludes whitespace,
# newlines, '=' and other control characters that would inject
# additional lines or break the key=value shape of a .gitreview
# entry.  Hostname syntax is left to git review / DNS to validate;
# this guard exists purely to stop a malformed CLI argument
# from corrupting the generated file.
HOST_VALUE_RE = re.compile(r"^[A-Za-z0-9.\-:/]+$")

# Matches a TCP port: a 1- to 5-digit non-negative integer.  The
# numeric range (1-65535) is enforced separately so the regex
# alone does not have to reason about leading zeros or 65536+.
PORT_VALUE_RE = re.compile(r"^[0-9]{1,5}$")

# Identity used for synthetic 'new commit' strategy commits.
SYNTHETIC_AUTHOR_NAME = "test-deploy-gerrit"
SYNTHETIC_AUTHOR_EMAIL = "test-deploy@modeseven.org"


@dataclass
class CommitMetadata:
    """Author/committer metadata for a single commit."""

    author_name: str
    author_email: str
    author_date: str
    committer_name: str
    committer_email: str
    committer_date: str
    message: bytes
    parents: list[str]


@dataclass
class Counters:
    """Per-step tally returned in the final summary."""

    scanned: int = 0
    updated: int = 0
    skipped: int = 0


class GitReviewToolError(RuntimeError):
    """Raised on unrecoverable internal errors (malformed git output)."""


def _warn(msg: str) -> None:
    """Emit a GitHub Actions ``::warning::`` annotation."""
    print(f"::warning::{msg}", flush=True)


def _info(msg: str) -> None:
    """Emit a plain informational line to stdout."""
    print(msg, flush=True)


def _run_git(
    repo: Path,
    *args: str,
    input_bytes: bytes | None = None,
    env: dict[str, str] | None = None,
) -> bytes:
    """Run a git plumbing command in *repo*, returning stdout bytes.

    A non-zero exit raises :class:`subprocess.CalledProcessError`;
    callers that need to probe (for example ``cat-file -e``) wrap
    the call in their own ``try/except`` block.

    The current process environment is inherited; any keys in *env*
    override the inherited values for the child process only.
    """
    cmd = ["git", "-C", str(repo), *args]
    child_env = os.environ.copy()
    if env:
        child_env.update(env)
    return subprocess.run(
        cmd,
        input=input_bytes,
        capture_output=True,
        check=True,
        env=child_env,
    ).stdout


def find_bare_repos(root: Path) -> list[Path]:
    """Enumerate bare repos (``*.git`` directories) under *root*.

    Mirrors ``find $root -type d -name '*.git' -prune`` from the
    previous shell implementation: descends through the tree but does
    not recurse INTO a matched ``.git`` directory.  Returned paths are
    sorted lexicographically so the scan order is deterministic
    across runs.
    """
    result: list[Path] = []
    if not root.exists():
        return result
    for dirpath, dirnames, _ in os.walk(root):
        # Iterate over a snapshot so the in-place mutation of
        # dirnames (to prune descent) is safe.
        for name in list(dirnames):
            if name.endswith(".git"):
                result.append(Path(dirpath) / name)
                dirnames.remove(name)
    result.sort()
    return result


def head_ref(repo: Path) -> str | None:
    """Return the symbolic ref of HEAD (e.g. ``refs/heads/main``).

    Returns ``None`` when HEAD is detached or otherwise unresolved.
    """
    try:
        out = _run_git(repo, "symbolic-ref", "-q", "HEAD")
    except subprocess.CalledProcessError:
        return None
    ref = out.decode().rstrip("\n")
    return ref or None


def has_blob(repo: Path, ref: str, path: str) -> bool:
    """Return ``True`` iff ``ref:path`` resolves to a blob in *repo*.

    Verifies the object type rather than just its existence: a path
    that resolves to a tree (a directory named ``.gitreview``, say)
    or a gitlink (submodule) returns ``False`` so the downstream
    ``cat-file blob`` read cannot abort the run on type mismatch.
    """
    try:
        out = _run_git(repo, "cat-file", "-t", f"{ref}:{path}")
    except subprocess.CalledProcessError:
        return False
    return out.decode().strip() == "blob"


def read_blob(repo: Path, ref: str, path: str) -> bytes:
    """Read the raw bytes of the blob at ``ref:path``."""
    return _run_git(repo, "cat-file", "blob", f"{ref}:{path}")


def list_branch_refs(repo: Path) -> list[str]:
    """List ``refs/heads/*`` refnames in *repo*."""
    out = _run_git(repo, "for-each-ref", "--format=%(refname)", "refs/heads/")
    return [line for line in out.decode().split("\n") if line]


def get_blob_mode(repo: Path, ref: str, path: str) -> str:
    """Read the file mode of *path* in *ref*'s tree.

    Defaults to ``100644`` when the lookup fails or the entry is
    missing, matching the previous shell implementation.
    """
    try:
        out = _run_git(repo, "ls-tree", ref, path).decode()
    except subprocess.CalledProcessError:
        return "100644"
    if not out:
        return "100644"
    return out.split(maxsplit=1)[0]


def get_commit_metadata(repo: Path, ref: str) -> CommitMetadata:
    """Read author/committer/date/message/parents for *ref*'s tip.

    Uses a single ``git show -s`` with NUL-separated fields so the
    full metadata is fetched in one subprocess.  ``%B`` (raw body) is
    placed last so any embedded NULs in the message (highly unusual
    but legal) do not confuse the field split.
    """
    fmt = "%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI%x00%P%x00%B"
    raw = _run_git(repo, "show", "-s", f"--format={fmt}", ref)
    # git always appends a trailing newline after the format
    # expansion; strip it so the raw body in %B is byte-exact.
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    parts = raw.split(b"\x00", 7)
    if len(parts) != 8:
        raise GitReviewToolError(
            f"unexpected git show output for {ref} in {repo}: {raw!r}"
        )
    an, ae, ad, cn, ce, cd, parents_b, msg_b = parts
    parents_str = parents_b.decode().strip()
    return CommitMetadata(
        author_name=an.decode(),
        author_email=ae.decode(),
        author_date=ad.decode(),
        committer_name=cn.decode(),
        committer_email=ce.decode(),
        committer_date=cd.decode(),
        message=msg_b,
        parents=parents_str.split() if parents_str else [],
    )


def hash_blob(repo: Path, content: bytes) -> str:
    """Write *content* as a blob in *repo*'s object DB, return its SHA."""
    out = _run_git(repo, "hash-object", "-w", "--stdin", input_bytes=content)
    return out.decode().strip()


def make_tree(
    repo: Path,
    base_tree: str,
    path: str,
    mode: str,
    blob_sha: str,
) -> str:
    """Build a new tree based on *base_tree* with ``path`` set to ``blob_sha``.

    Uses a temporary index file scoped to a private workdir so the
    operation never disturbs any other state in the repo.  The
    workdir is unconditionally cleaned up on every exit path.
    """
    work = Path(tempfile.mkdtemp(prefix="gitreview-tool-"))
    try:
        index = work / "idx"
        env = {"GIT_INDEX_FILE": str(index)}
        _run_git(repo, "read-tree", base_tree, env=env)
        _run_git(
            repo,
            "update-index",
            "--add",
            "--cacheinfo",
            f"{mode},{blob_sha},{path}",
            env=env,
        )
        return _run_git(repo, "write-tree", env=env).decode().strip()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def commit_tree(
    repo: Path,
    tree_sha: str,
    parents: Sequence[str],
    message: bytes,
    author_name: str,
    author_email: str,
    committer_name: str,
    committer_email: str,
    author_date: str | None = None,
    committer_date: str | None = None,
) -> str:
    """Create a commit object with the supplied tree, parents and identity.

    The message is supplied via ``-F -`` so it crosses the subprocess
    boundary verbatim, preserving any unusual whitespace or trailing
    newlines from the original commit when the caller is amending.
    """
    args: list[str] = ["commit-tree", tree_sha]
    for parent in parents:
        args.extend(["-p", parent])
    args.extend(["-F", "-"])
    env: dict[str, str] = {
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": committer_name,
        "GIT_COMMITTER_EMAIL": committer_email,
    }
    if author_date is not None:
        env["GIT_AUTHOR_DATE"] = author_date
    if committer_date is not None:
        env["GIT_COMMITTER_DATE"] = committer_date
    out = _run_git(repo, *args, input_bytes=message, env=env)
    return out.decode().strip()


def update_ref_safely(
    repo: Path,
    ref: str,
    new_sha: str,
    expected_old: str,
) -> tuple[bool, str | None]:
    """Compare-and-swap update-ref, tolerating concurrent-replication races.

    Returns ``(True, None)`` on success, ``(False, stderr)`` when the
    update is refused (typically because background pull-replication
    moved the tip between the parent read and the swap).
    """
    try:
        subprocess.run(
            ["git", "-C", str(repo), "update-ref", ref, new_sha, expected_old],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        return False, (exc.stderr or "").strip() or "unknown"
    return True, None


# ---- .gitreview content manipulation --------------------------------


def _line_terminator(line: bytes) -> bytes:
    """Return the line terminator suffix of *line* (b"\r\n", b"\n", or b"").

    Inspects the last one or two bytes of *line* (as returned by
    ``bytes.splitlines(keepends=True)``) so the rewritten line can
    keep the same terminator as the original.  Preserving CRLF
    matters for the byte-exact no-op guarantee: a host/port value
    that is already correct must produce identical bytes, even when
    the source uses Windows line endings.
    """
    if line.endswith(b"\r\n"):
        return b"\r\n"
    if line.endswith(b"\n"):
        return b"\n"
    return b""


def rewrite_gitreview_bytes(
    content: bytes,
    new_host: str,
    new_port: str | None,
    keep_port: bool,
) -> bytes:
    """Rewrite ``host=`` (and optionally ``port=``) lines in *content*.

    The source's EOF-newline semantics are preserved: if the input
    lacks a trailing newline, the output will lack one too, so a
    no-op rewrite produces byte-exact output and the calling layer
    can elide the synthetic commit.  Per-line terminators are also
    preserved: a ``host=`` line that originally ended in CRLF is
    rewritten with CRLF, so a CRLF-formatted ``.gitreview`` with
    already-correct values still round-trips byte-exact.

    Lines that are not ``host=``/``port=`` are passed through with
    their original line terminator intact.  When *keep_port* is set
    or *new_port* is ``None`` the existing ``port=`` line is left
    alone (Tailscale flow).
    """
    if not content:
        return content
    had_trailing_newline = content.endswith(b"\n")
    new_lines: list[bytes] = []
    for line in content.splitlines(keepends=True):
        if HOST_LINE_RE.match(line):
            term = _line_terminator(line)
            new_lines.append(f"host={new_host}".encode() + term)
            continue
        if PORT_LINE_RE.match(line):
            if keep_port or new_port is None:
                new_lines.append(line)
            else:
                term = _line_terminator(line)
                new_lines.append(f"port={new_port}".encode() + term)
            continue
        new_lines.append(line)
    out = b"".join(new_lines)
    if not had_trailing_newline and out.endswith(b"\n"):
        out = out[:-1]
    return out


def build_minimal_gitreview(
    host: str,
    port: str,
    project: str,
    default_branch: str,
) -> bytes:
    """Return the canonical minimal .gitreview body for a new file."""
    return (
        f"[gerrit]\n"
        f"host={host}\n"
        f"port={port}\n"
        f"project={project}\n"
        f"defaultbranch={default_branch}\n"
    ).encode()


def extract_template_host_port(
    content: bytes,
) -> tuple[str | None, str | None]:
    """Return the first ``host=`` and ``port=`` values from *content*.

    Either value may be ``None`` when the corresponding line is absent
    or its right-hand side is empty.  The harvest loop in
    :func:`cmd_create_missing` treats a ``None`` host as a malformed
    template and keeps scanning.
    """
    host: str | None = None
    port: str | None = None
    for raw_line in content.splitlines():
        if host is None and HOST_LINE_RE.match(raw_line):
            value = raw_line.split(b"=", 1)[1].decode(errors="replace").strip()
            host = value or None
        elif port is None and PORT_LINE_RE.match(raw_line):
            value = raw_line.split(b"=", 1)[1].decode(errors="replace").strip()
            port = value or None
        if host is not None and port is not None:
            break
    return host, port


# ---- High-level commit strategies -----------------------------------


def write_new_commit(
    repo: Path,
    new_tree: str,
    parent: str,
    subject: str,
    extra_lines: Iterable[str] = (),
) -> str:
    """Append a synthetic 'new' commit on top of *parent*, returning the SHA."""
    body_lines = [subject, ""]
    body_lines.extend(extra_lines)
    message = ("\n".join(body_lines) + "\n").encode()
    return commit_tree(
        repo,
        tree_sha=new_tree,
        parents=[parent],
        message=message,
        author_name=SYNTHETIC_AUTHOR_NAME,
        author_email=SYNTHETIC_AUTHOR_EMAIL,
        committer_name=SYNTHETIC_AUTHOR_NAME,
        committer_email=SYNTHETIC_AUTHOR_EMAIL,
    )


def write_amended_commit(repo: Path, commit_ish: str, new_tree: str) -> str:
    """Rebuild *commit_ish*'s tree with *new_tree* and the same author/committer/etc.

    *commit_ish* must resolve to a single commit (typically a SHA
    captured at the start of the calling pass so the rebuild is
    immune to concurrent replication races).  Multi-parent (merge)
    commits are preserved: every parent from the original commit is
    passed through unchanged so the topology is identical and only
    the tree differs.
    """
    meta = get_commit_metadata(repo, commit_ish)
    return commit_tree(
        repo,
        tree_sha=new_tree,
        parents=meta.parents,
        message=meta.message,
        author_name=meta.author_name,
        author_email=meta.author_email,
        committer_name=meta.committer_name,
        committer_email=meta.committer_email,
        author_date=meta.author_date,
        committer_date=meta.committer_date,
    )


def _project_name_from_repo(repo: Path) -> str:
    """Derive the Gerrit ``project=`` value from a bare repo path."""
    rel = str(repo.relative_to(GIT_ROOT))
    # Gerrit project names omit the trailing '.git'.
    return rel.removesuffix(".git")


# ---- Commands -------------------------------------------------------


def cmd_create_missing(strategy: str) -> int:
    """Implement the ``create-missing`` sub-command."""
    if not GIT_ROOT.is_dir():
        _warn(f"{GIT_ROOT} not found; nothing to create")
        return 0

    repos = find_bare_repos(GIT_ROOT)
    if not repos:
        _warn(f"no bare repos found under {GIT_ROOT}")
        return 0

    template_host, template_port = _harvest_template(repos)
    if template_host is None:
        _warn(
            "no .gitreview template found in any repo; "
            "cannot create missing .gitreview files"
        )
        return 0
    if not template_port:
        template_port = DEFAULT_GERRIT_SSH_PORT
    _info(f"template host={template_host} port={template_port}")

    counters = Counters()
    for repo in repos:
        counters.scanned += 1
        if repo.name in INTERNAL_REPOS:
            continue
        ref = head_ref(repo)
        if ref is None:
            continue
        try:
            _run_git(repo, "rev-parse", "-q", "--verify", ref)
        except subprocess.CalledProcessError:
            continue
        if has_blob(repo, ref, ".gitreview"):
            continue  # already present; nothing to create
        _create_one(
            repo=repo,
            ref=ref,
            template_host=template_host,
            template_port=template_port,
            strategy=strategy,
            counters=counters,
        )

    _info(f"scanned repos: {counters.scanned}")
    _info(f"branches created: {counters.updated}")
    _info(f"branches skipped: {counters.skipped}")
    return 0


def _harvest_template(repos: list[Path]) -> tuple[str | None, str | None]:
    """Find the first .gitreview on any repo's HEAD tip that carries
    a non-empty ``host=`` line.

    Only HEAD (the default branch) of each repo is inspected, not
    every ref under ``refs/heads/*``: a usable template anywhere on
    a default branch is sufficient, and limiting the scan keeps the
    harvest fast on estates with deep branch histories.

    Malformed .gitreview files (those without a ``host=`` line)
    are silently skipped so a single bad file does not stop the
    harvest.
    """
    for repo in repos:
        ref = head_ref(repo)
        if ref is None:
            continue
        if not has_blob(repo, ref, ".gitreview"):
            continue
        blob = read_blob(repo, ref, ".gitreview")
        host, port = extract_template_host_port(blob)
        if host is None:
            continue
        return host, port
    return None, None


def _create_one(
    repo: Path,
    ref: str,
    template_host: str,
    template_port: str,
    strategy: str,
    counters: Counters,
) -> None:
    """Synthesise a .gitreview on *ref* and update the ref atomically.

    Snapshots the tip SHA up-front and derives every subsequent read
    (base tree, amend metadata) from that immutable SHA rather than
    from *ref*, so a concurrent pull-replication push between reads
    cannot produce a commit whose tree comes from a different
    snapshot than the one the CAS update-ref check guards.
    """
    parent_sha = _run_git(repo, "rev-parse", ref).decode().strip()
    project_name = _project_name_from_repo(repo)
    default_branch = ref.removeprefix("refs/heads/")
    new_content = build_minimal_gitreview(
        host=template_host,
        port=template_port,
        project=project_name,
        default_branch=default_branch,
    )

    new_blob_sha = hash_blob(repo, new_content)
    base_tree = _run_git(repo, "rev-parse", f"{parent_sha}^{{tree}}").decode().strip()
    new_tree = make_tree(repo, base_tree, ".gitreview", "100644", new_blob_sha)

    if strategy == "amend":
        new_commit = write_amended_commit(repo, parent_sha, new_tree)
    else:
        new_commit = write_new_commit(
            repo,
            new_tree=new_tree,
            parent=parent_sha,
            # Subject is intentionally neutral: this command also
            # runs when 'rewrite_gitreview=Leave unchanged', in
            # which case the file legitimately points at the
            # upstream Gerrit and "for test deployment" would be
            # misleading.  Any rewrite-driven retargeting happens
            # in a follow-up step.
            subject="Chore: Add missing .gitreview",
            extra_lines=(
                "Auto-generated from estate template:",
                f"  host={template_host}",
                f"  port={template_port}",
                f"  project={project_name}",
            ),
        )

    ok, err = update_ref_safely(repo, ref, new_commit, parent_sha)
    project_path = str(repo.relative_to(GIT_ROOT))
    if not ok:
        _warn(
            f"skip {project_path}: update-ref failed (concurrent replication?): {err}"
        )
        counters.skipped += 1
        return
    counters.updated += 1
    _info(
        f"created .gitreview: {project_path} ({default_branch}) [strategy={strategy}]"
    )


def _validate_host(host: str) -> str | None:
    """Return ``None`` if *host* is safe to write into a ``.gitreview``
    ``host=`` line, otherwise a human-readable error description.

    Rejects whitespace, newlines, ``=``, and any other character
    outside the ``HOST_VALUE_RE`` whitelist so a malformed CLI
    argument cannot inject additional lines into the file or break
    the key=value shape downstream.
    """
    if not host:
        return "host is empty"
    if not HOST_VALUE_RE.match(host):
        return (
            f"host {host!r} contains characters outside the allowed set "
            "(letters, digits, '.', '-', ':', '/')"
        )
    return None


def _validate_port(port: str) -> str | None:
    """Return ``None`` if *port* is a syntactically valid TCP port
    (1-65535), otherwise a human-readable error description.
    """
    if not PORT_VALUE_RE.match(port):
        return f"port {port!r} is not a 1-5 digit decimal integer"
    value = int(port)
    if not 1 <= value <= 65535:
        return f"port {port!r} is outside the valid TCP range (1-65535)"
    return None


def cmd_rewrite(
    host: str,
    port: str | None,
    keep_port: bool,
    strategy: str,
) -> int:
    """Implement the ``rewrite`` sub-command."""
    if not GIT_ROOT.is_dir():
        _warn(f"{GIT_ROOT} not found; nothing to rewrite")
        return 0
    host_err = _validate_host(host)
    if host_err is not None:
        _warn(f"{host_err}; skipping .gitreview rewrite")
        return 0
    if not keep_port and port is not None:
        port_err = _validate_port(port)
        if port_err is not None:
            _warn(f"{port_err}; skipping .gitreview rewrite")
            return 0
    # argparse enforces 'exactly one of --port / --keep-port' via
    # a required mutually-exclusive group; no runtime guard needed.

    _info(f"target host={host} port={'<unchanged>' if keep_port else port}")

    counters = Counters()
    for repo in find_bare_repos(GIT_ROOT):
        counters.scanned += 1
        for ref in list_branch_refs(repo):
            if not has_blob(repo, ref, ".gitreview"):
                continue
            _rewrite_one(
                repo=repo,
                ref=ref,
                host=host,
                port=port,
                keep_port=keep_port,
                strategy=strategy,
                counters=counters,
            )

    _info(f"scanned repos: {counters.scanned}")
    _info(f"branches updated: {counters.updated}")
    _info(f"branches skipped: {counters.skipped}")
    return 0


def _rewrite_one(
    repo: Path,
    ref: str,
    host: str,
    port: str | None,
    keep_port: bool,
    strategy: str,
    counters: Counters,
) -> None:
    """Rewrite the .gitreview on a single branch tip atomically.

    Snapshots the tip SHA up-front and reads the existing blob, file
    mode, base tree and (for amend) commit metadata from that
    immutable SHA rather than from *ref*.  Without this, a concurrent
    pull-replication push between the blob read and the CAS
    update-ref could land a rewrite computed against a stale
    snapshot whose parent is now the one the CAS check guards
    against - producing a commit whose tree is inconsistent with
    expected_old.
    """
    parent_sha = _run_git(repo, "rev-parse", ref).decode().strip()
    if not has_blob(repo, parent_sha, ".gitreview"):
        # ref had a .gitreview when the caller checked, but the
        # snapshot we just captured does not (extremely unlikely;
        # would require a concurrent push that removed the file).
        return
    old_content = read_blob(repo, parent_sha, ".gitreview")
    new_content = rewrite_gitreview_bytes(
        old_content, new_host=host, new_port=port, keep_port=keep_port
    )
    if new_content == old_content:
        return  # byte-exact no-op; do not synthesise a commit

    mode = get_blob_mode(repo, parent_sha, ".gitreview")
    new_blob_sha = hash_blob(repo, new_content)
    base_tree = _run_git(repo, "rev-parse", f"{parent_sha}^{{tree}}").decode().strip()
    new_tree = make_tree(repo, base_tree, ".gitreview", mode, new_blob_sha)

    if strategy == "amend":
        new_commit = write_amended_commit(repo, parent_sha, new_tree)
    else:
        extra: list[str] = [
            "Repointed at the short-lived test Gerrit instance:",
            f"  host={host}",
        ]
        if not keep_port and port:
            extra.append(f"  port={port}")
        new_commit = write_new_commit(
            repo,
            new_tree=new_tree,
            parent=parent_sha,
            subject="Chore: Rewrite .gitreview for test deployment",
            extra_lines=extra,
        )

    ok, err = update_ref_safely(repo, ref, new_commit, parent_sha)
    project = str(repo.relative_to(GIT_ROOT))
    branch = ref.removeprefix("refs/heads/")
    if not ok:
        _warn(
            f"skip {project} {branch}: update-ref failed "
            f"(concurrent replication?): {err}"
        )
        counters.skipped += 1
        return
    counters.updated += 1
    _info(f"rewrote .gitreview: {project} {branch} [strategy={strategy}]")


# ---- CLI ------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="gitreview_tool",
        description=(
            "Create and/or rewrite .gitreview files across bare Gerrit "
            "repositories under /var/gerrit/git."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    create = sub.add_parser(
        "create-missing",
        help="Synthesise .gitreview where missing (template from estate).",
    )
    create.add_argument(
        "--strategy",
        choices=("new", "amend"),
        default="new",
        help="Commit strategy (default: new).",
    )

    rewrite = sub.add_parser(
        "rewrite",
        help="Rewrite host (and optionally port) in every .gitreview tip.",
    )
    rewrite.add_argument("--host", required=True, help="New host= value.")
    # --port and --keep-port are mutually exclusive: rewrite either
    # sets a concrete new port or explicitly preserves whatever is
    # already on the line (Tailscale mode).  An invocation that
    # passes neither (or both) is a workflow bug; failing fast at
    # parse time avoids a silent miss where the workflow thinks it
    # set a port but the tool ignored it.
    port_group = rewrite.add_mutually_exclusive_group(required=True)
    port_group.add_argument(
        "--port",
        default=None,
        help="New port= value.",
    )
    port_group.add_argument(
        "--keep-port",
        action="store_true",
        help=(
            "Leave existing port= lines untouched (Tailscale mode: the "
            "tunnel exposes the container's native SSH port directly)."
        ),
    )
    rewrite.add_argument(
        "--strategy",
        choices=("new", "amend"),
        required=True,
        help="Commit strategy.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: parse args and dispatch to the sub-command handler."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "create-missing":
        return cmd_create_missing(args.strategy)
    if args.cmd == "rewrite":
        return cmd_rewrite(
            host=args.host,
            port=args.port,
            keep_port=args.keep_port,
            strategy=args.strategy,
        )
    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
