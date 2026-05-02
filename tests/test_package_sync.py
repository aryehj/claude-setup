"""Static analysis: apt package lists in Dockerfile and start-claude.sh must match."""
import re
from pathlib import Path

REPO = Path(__file__).parent.parent
DOCKERFILE = REPO / "dockerfiles" / "claude-agent.Dockerfile"
START_CLAUDE = REPO / "start-claude.sh"

_PKG_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+\-\.~]*$")


def _parse_apt_packages(text: str) -> set[str]:
    """Extract packages from the apt-get install --no-install-recommends block."""
    lines = text.splitlines()
    in_block = False
    packages: set[str] = set()

    for line in lines:
        stripped = line.strip()

        if not in_block:
            if "apt-get install -y --no-install-recommends" in stripped:
                in_block = True
                payload = re.sub(r".*--no-install-recommends", "", stripped).rstrip("\\").strip()
                packages.update(t for t in payload.split() if _PKG_NAME.match(t))
            continue

        tokens = stripped.rstrip("\\").split()
        if stripped.startswith("&&") or not all(_PKG_NAME.match(t) for t in tokens):
            break
        packages.update(tokens)
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
