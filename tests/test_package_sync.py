"""Static analysis: apt package lists in Dockerfile and start-claude.sh must match."""
import re
from pathlib import Path

REPO = Path(__file__).parent.parent
DOCKERFILE = REPO / "dockerfiles" / "claude-agent.Dockerfile"
START_CLAUDE = REPO / "start-claude.sh"

_PKG_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+\-\.~]*$")


def _parse_apt_packages(text: str) -> set[str]:
    """Extract packages from the apt-get install --no-install-recommends block.

    Walks line by line: starts collecting after the install header line, stops
    when it reaches a line that neither ends with \\ nor contains only package
    names (catches && continuations and bare shell commands).
    """
    lines = text.splitlines()
    in_block = False
    packages: set[str] = set()

    for line in lines:
        stripped = line.strip()

        if not in_block:
            if "apt-get install -y --no-install-recommends" in stripped:
                in_block = True
                # Packages may start on the same line after the flag; fall through.
                # Remove the apt-get prefix up to the flag, then collect tokens.
                payload = re.sub(r".*--no-install-recommends", "", stripped)
                payload = payload.rstrip("\\").strip()
                for tok in payload.split():
                    if _PKG_NAME.match(tok):
                        packages.add(tok)
            continue

        # We're inside the block. A line that starts with && or a bare command
        # (apt-get, rm, touch, …) signals the end of the package list.
        if stripped.startswith("&&") or (
            not stripped.endswith("\\") and not all(_PKG_NAME.match(t) for t in stripped.split())
        ):
            # Check whether ALL non-\\ tokens are valid package names; if not, we
            # might be on a command line — stop.
            tokens = stripped.rstrip("\\").split()
            all_pkgs = tokens and all(_PKG_NAME.match(t) for t in tokens)
            if not all_pkgs:
                break

        # Collect package names from this continuation line.
        payload = stripped.rstrip("\\").strip()
        for tok in payload.split():
            if _PKG_NAME.match(tok):
                packages.add(tok)

        # If no trailing backslash and we're not at the header, the block ended.
        if not stripped.endswith("\\"):
            break

    return packages


def test_dockerfile_and_start_claude_package_lists_match():
    dockerfile_packages = _parse_apt_packages(DOCKERFILE.read_text())
    start_claude_packages = _parse_apt_packages(START_CLAUDE.read_text())

    assert dockerfile_packages, "No packages found in Dockerfile apt-get install block"
    assert start_claude_packages, "No packages found in start-claude.sh apt-get install block"

    only_in_dockerfile = dockerfile_packages - start_claude_packages
    only_in_start_claude = start_claude_packages - dockerfile_packages

    assert not only_in_dockerfile and not only_in_start_claude, (
        f"Package lists diverged.\n"
        f"  Only in Dockerfile:     {sorted(only_in_dockerfile)}\n"
        f"  Only in start-claude.sh: {sorted(only_in_start_claude)}"
    )
