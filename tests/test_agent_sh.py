"""Static analysis tests for start-agent.sh network isolation properties.

Verifies that neither the searxng nor the claude-agent docker run commands
publish ports to the host (-p / --publish). Publishing a port would make the
service reachable from the macOS host and from other Colima VMs — isolation
that should come from the absence of port forwarding, not from firewall rules.
"""
import re
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "start-agent.sh"


def _collect_docker_run_block(lines: list[str], start: int) -> str:
    """Collect a multi-line docker run block following backslash continuations."""
    block = []
    i = start
    while i < len(lines):
        block.append(lines[i])
        if not lines[i].rstrip().endswith("\\"):
            break
        i += 1
    return "\n".join(block)


def _find_docker_run_blocks(script_text: str) -> list[str]:
    """Return all docker run command blocks in the script, skipping comment lines."""
    lines = script_text.splitlines()
    blocks = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if "docker run" in stripped and not stripped.startswith("#"):
            blocks.append(_collect_docker_run_block(lines, i))
    return blocks


def _block_for_marker(blocks: list[str], marker: str) -> str | None:
    return next((b for b in blocks if marker in b), None)


def _has_host_port_binding(block: str) -> bool:
    """True if the docker run block publishes a host port (-p HOST:PORT or --publish)."""
    # Match -p followed by a digit (port number), not -p as a protocol flag
    if re.search(r"(?:^|\s)-p\s+\d", block):
        return True
    if "--publish" in block:
        return True
    return False


def test_searxng_no_host_port_binding():
    """SearXNG must not publish any port to the host.

    No -p flag means docker adds no NAT rules, so port 8080 is only reachable
    from containers on claude-agent-net — not from the macOS host or other VMs.
    """
    blocks = _find_docker_run_blocks(SCRIPT.read_text())
    block = _block_for_marker(blocks, "SEARXNG_CONTAINER")
    assert block is not None, "SearXNG docker run not found in start-agent.sh"
    assert not _has_host_port_binding(block), (
        "SearXNG docker run exposes a host port (-p or --publish); "
        "this makes SearXNG reachable from the macOS host and other Colima VMs:\n"
        + block
    )


def test_claude_agent_no_host_port_binding():
    """claude-agent must not publish any port to the host."""
    blocks = _find_docker_run_blocks(SCRIPT.read_text())
    block = _block_for_marker(blocks, "IMAGE_TAG")
    assert block is not None, "claude-agent docker run not found in start-agent.sh"
    assert not _has_host_port_binding(block), (
        "claude-agent docker run exposes a host port (-p or --publish):\n" + block
    )
