"""Static analysis: docker run commands in start-agent.sh must not publish host ports.
Also verifies --reset-container flag structure invariants, Phase-1 denylist machinery,
and Phase-2 Squid-proxy implementation (tinyproxy removed, squid config present)."""
import re
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "start-agent.sh"
_SCRIPT_TEXT = SCRIPT.read_text()


def _collect_docker_run_block(lines: list[str], start: int) -> str:
    block = []
    i = start
    while i < len(lines):
        block.append(lines[i])
        if not lines[i].rstrip().endswith("\\"):
            break
        i += 1
    return "\n".join(block)


def _find_docker_run_blocks(script_text: str) -> list[str]:
    lines = script_text.splitlines()
    blocks = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if "docker run" in stripped and not stripped.startswith("#"):
            blocks.append(_collect_docker_run_block(lines, i))
    return blocks


def _has_host_port_binding(block: str) -> bool:
    # -p followed by a digit to distinguish port-publish from protocol flags like -p tcp
    return bool(re.search(r"(?:^|\s)-p\s+\d", block)) or "--publish" in block


_BLOCKS = _find_docker_run_blocks(_SCRIPT_TEXT)


def _assert_no_host_port_binding(marker: str, service_name: str) -> None:
    block = next((b for b in _BLOCKS if marker in b), None)
    assert block is not None, f"{service_name} docker run not found in start-agent.sh"
    assert not _has_host_port_binding(block), (
        f"{service_name} docker run exposes a host port (-p or --publish); "
        "reachable from the macOS host and other Colima VMs:\n" + block
    )


def test_searxng_no_host_port_binding():
    _assert_no_host_port_binding("SEARXNG_CONTAINER", "SearXNG")


def test_claude_agent_no_host_port_binding():
    _assert_no_host_port_binding("IMAGE_TAG", "claude-agent")


# ── --reset-container invariants ──────────────────────────────────────────────

def test_reset_container_arg_case_exists():
    assert "--reset-container)" in _SCRIPT_TEXT, (
        "--reset-container) case missing from arg parser"
    )


def test_reset_container_in_help_text():
    # The usage() function text must document the flag.
    assert "--reset-container" in _SCRIPT_TEXT, (
        "--reset-container missing from usage/help block"
    )


def test_reset_container_mutual_exclusion_check():
    # The script must contain a mutual-exclusion guard for --reset-container + --rebuild.
    assert "RESET_CONTAINER" in _SCRIPT_TEXT and "REBUILD" in _SCRIPT_TEXT, (
        "RESET_CONTAINER or REBUILD variable missing"
    )
    # The guard must produce an error message referencing both flags.
    assert re.search(r"reset-container.*rebuild|rebuild.*reset-container", _SCRIPT_TEXT, re.IGNORECASE), (
        "No mutual-exclusion error message referencing both --reset-container and --rebuild found"
    )


def test_reset_container_skips_image_rm():
    # The reset-container branch must NOT contain docker image rm.
    # We verify by asserting the image-rm only appears inside the REBUILD-specific block,
    # not duplicated in a reset-container-only block.
    # Simple invariant: any 'docker image rm' line must be inside a $REBUILD guard.
    lines = _SCRIPT_TEXT.splitlines()
    image_rm_lines = [i for i, l in enumerate(lines) if "docker image rm" in l and not l.strip().startswith("#")]
    for lineno in image_rm_lines:
        # Walk back to find the nearest enclosing if-condition
        context = "\n".join(lines[max(0, lineno - 20):lineno + 1])
        assert "REBUILD" in context, (
            f"docker image rm at line {lineno + 1} is not inside a REBUILD guard:\n{context}"
        )


# ── Phase 1: denylist machinery ───────────────────────────────────────────────

def test_bash_syntax():
    """bash -n must pass — no syntax errors in the script."""
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bash -n start-agent.sh failed:\n{result.stderr}"
    )


def test_denylist_constants_present():
    """New denylist constants must exist in the script."""
    for constant in (
        "DENYLIST_DIR",
        "DENYLIST_SOURCES_FILE",
        "DENYLIST_ADDITIONS_FILE",
        "DENYLIST_OVERRIDES_FILE",
        "DENYLIST_CACHE_DIR",
        "SQUID_PORT",
        "TEMPLATE_DENYLIST_SOURCES",
        "TEMPLATE_DENYLIST_ADDITIONS",
    ):
        assert constant in _SCRIPT_TEXT, f"Constant {constant} missing from start-agent.sh"


def test_denylist_helper_functions_defined():
    """Denylist helper bash functions must be defined in the script."""
    for fn in (
        "seed_denylist_files",
        "prune_orphan_cache_files",
        "refresh_denylist_cache",
        "compose_denylist_to_file",
    ):
        assert f"{fn}()" in _SCRIPT_TEXT, f"Function {fn}() not defined in start-agent.sh"


def test_legacy_allowlist_guard_present():
    """The script must contain a guard that rejects legacy allowlist.txt installations."""
    assert "allowlist.txt" in _SCRIPT_TEXT, "allowlist.txt guard missing"
    # Must exit 1 in response to the legacy file.
    assert re.search(r"allowlist\.txt.*exit 1|exit 1.*allowlist\.txt", _SCRIPT_TEXT, re.DOTALL), (
        "No 'exit 1' found near allowlist.txt check in start-agent.sh"
    )


def test_legacy_guard_exits_on_allowlist(tmp_path):
    """Script must exit 1 with a migration message when ~/.claude-agent/allowlist.txt exists."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    claude_agent_dir = fake_home / ".claude-agent"
    claude_agent_dir.mkdir()
    (claude_agent_dir / "allowlist.txt").write_text("# legacy\nexample.com\n")

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={
            "HOME": str(fake_home),
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            # Prevent colima/docker from being found so we hit the guard first.
            "CLAUDE_AGENT_DISABLE_SEARCH": "1",
        },
        cwd=str(tmp_path),
    )
    assert result.returncode == 1, (
        f"Expected exit 1 from legacy guard, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "allowlist.txt" in combined, (
        f"Migration message missing 'allowlist.txt'.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── Phase 2: Squid proxy replaces tinyproxy ───────────────────────────────────

def test_no_tinyproxy_install():
    """apt-get install tinyproxy must not appear in live code (only purge is allowed)."""
    assert not re.search(r"apt-get install\b.*tinyproxy", _SCRIPT_TEXT), (
        "apt-get install tinyproxy found in start-agent.sh — tinyproxy should only be purged"
    )


def test_no_tinyproxy_enable():
    """systemctl enable/start/restart tinyproxy must not appear."""
    assert not re.search(r"systemctl\s+(enable|start|restart)\s+tinyproxy", _SCRIPT_TEXT), (
        "systemctl enable/start/restart tinyproxy found — should use squid instead"
    )


def test_squid_config_content_present():
    """The squid.conf heredoc content must be present in start-agent.sh."""
    assert "http_access allow all" in _SCRIPT_TEXT, (
        "squid.conf content (http_access allow all) missing from start-agent.sh"
    )
    assert "visible_hostname claude-agent-squid" in _SCRIPT_TEXT, (
        "squid.conf content (visible_hostname) missing from start-agent.sh"
    )


def test_vm_has_hashlimit_defined():
    """vm_has_hashlimit() must be defined in start-agent.sh."""
    assert "vm_has_hashlimit()" in _SCRIPT_TEXT, (
        "vm_has_hashlimit() function not defined in start-agent.sh"
    )


def test_rate_limit_rule_present():
    """The iptables rate-limit rule must be present in the firewall heredoc."""
    assert "hashlimit" in _SCRIPT_TEXT or re.search(r"-m limit.*--limit.*\bRETURN\b", _SCRIPT_TEXT), (
        "No rate-limit iptables rule (hashlimit or -m limit fallback) found in start-agent.sh"
    )
