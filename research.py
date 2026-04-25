#!/usr/bin/env python3
"""
research.py — spin up an isolated Vane + SearXNG research environment.

Creates a dedicated Colima VM (profile: research) with its own egress firewall
(tinyproxy + iptables RESEARCH chain) and runs two containers:
  - research-searxng: SearXNG meta-search engine
  - research-vane: Vane AI research UI, accessible at http://localhost:3000

Host-side state lives in ~/.research/:
  allowlist.txt     — one domain per line; suffix-matched; edit and --reload-allowlist
  searxng/settings.yml  — seeded on first run
  vane-data/        — Vane persistent state (LLM config survives --rebuild)

Usage:
  ./research.py                         bring up the environment
  ./research.py --reload-allowlist      update tinyproxy filter without restarting
  ./research.py --reseed-allowlist      overwrite ~/.research/allowlist.txt with current template
  ./research.py --rebuild               recreate containers (optionally VM too)
  ./research.py --backend=omlx          use omlx instead of Ollama
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# ── Constants ──────────────────────────────────────────────────────────────────

TEMPLATE_ALLOWLIST = Path(__file__).parent / "templates" / "research-allowlist.txt"

COLIMA_PROFILE = "research"
CONTAINER_SEARXNG = "research-searxng"
CONTAINER_VANE = "research-vane"
RESEARCH_NET_NAME = "research-net"
TINYPROXY_PORT = 8888

DEFAULT_MEMORY_GIB = 2
DEFAULT_CPUS = 2
DEFAULT_VANE_PORT = 3000


# ── Paths ──────────────────────────────────────────────────────────────────────

@dataclass
class Paths:
    base: Path = field(default_factory=lambda: Path.home() / ".research")

    @property
    def allowlist_file(self) -> Path:
        return self.base / "allowlist.txt"

    @property
    def searxng_dir(self) -> Path:
        return self.base / "searxng"

    @property
    def searxng_settings(self) -> Path:
        return self.searxng_dir / "settings.yml"

    @property
    def vane_data_dir(self) -> Path:
        return self.base / "vane-data"


# ── VmConfig ───────────────────────────────────────────────────────────────────

@dataclass
class VmConfig:
    profile_name: str = COLIMA_PROFILE
    memory_gib: int = DEFAULT_MEMORY_GIB
    cpus: int = DEFAULT_CPUS
    backend: str = "ollama"
    vane_port: int = DEFAULT_VANE_PORT
    # Populated by discover_network():
    bridge_ip: Optional[str] = None
    bridge_cidr: Optional[str] = None
    host_ip: Optional[str] = None
    research_net_cidr: Optional[str] = None

    @property
    def inference_port(self) -> int:
        return 11434 if self.backend == "ollama" else 8000

    @property
    def inference_label(self) -> str:
        return "Ollama" if self.backend == "ollama" else "omlx"


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="research.py",
        description=(
            "Spin up an isolated Vane + SearXNG research environment on a "
            "dedicated Colima VM with egress firewall."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
ALLOWLIST:
  Edit ~/.research/allowlist.txt on the macOS host to change which domains
  are reachable. One domain per line; '#' for comments; suffix-matched
  (wikipedia.org also covers en.wikipedia.org). Apply changes with:
      ./research.py --reload-allowlist

ENVIRONMENT:
  RESEARCH_MEMORY        Default VM memory GiB (overridden by --memory)
  RESEARCH_CPUS          Default VM CPU count (overridden by --cpus)
  RESEARCH_BACKEND       Default backend: ollama (default) or omlx
  OMLX_API_KEY           API key for omlx backend
""",
    )
    p.add_argument(
        "--rebuild",
        action="store_true",
        help="Remove containers and recreate. With confirmation, also delete the Colima VM.",
    )
    p.add_argument(
        "--reload-allowlist",
        action="store_true",
        dest="reload_allowlist",
        help="Regenerate tinyproxy filter from ~/.research/allowlist.txt and reload tinyproxy. Fast path; does not restart containers.",
    )
    p.add_argument(
        "--reseed-allowlist",
        action="store_true",
        dest="reseed_allowlist",
        help=f"Overwrite ~/.research/allowlist.txt with the current template "
             f"({TEMPLATE_ALLOWLIST.name}). Use after pulling allowlist updates.",
    )
    p.add_argument(
        "--backend",
        choices=["ollama", "omlx"],
        default=os.environ.get("RESEARCH_BACKEND", "ollama"),
        help="Local inference backend (default: ollama).",
    )
    p.add_argument(
        "--memory",
        type=_parse_gib,
        default=_parse_gib(os.environ.get("RESEARCH_MEMORY", str(DEFAULT_MEMORY_GIB))),
        metavar="GIB",
        help=f"VM memory in GiB (default: {DEFAULT_MEMORY_GIB}).",
    )
    p.add_argument(
        "--cpus",
        type=int,
        default=int(os.environ.get("RESEARCH_CPUS", str(DEFAULT_CPUS))),
        metavar="N",
        help=f"VM CPU count (default: {DEFAULT_CPUS}).",
    )
    p.add_argument(
        "--port",
        type=int,
        default=DEFAULT_VANE_PORT,
        dest="vane_port",
        metavar="PORT",
        help=f"Host port for Vane UI (default: {DEFAULT_VANE_PORT}).",
    )
    return p.parse_args()


def _parse_gib(raw: str) -> int:
    """Accept '2', '2G', '2GB', '2GiB' and return integer GiB."""
    cleaned = raw.strip().lower().rstrip("b").rstrip("i").rstrip("g")
    try:
        return int(cleaned)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid memory value {raw!r} — use integer GiB (e.g. 2, 2G, 2GB)"
        )


# ── Allowlist seed ─────────────────────────────────────────────────────────────


def seed_allowlist(paths: Paths, force: bool = False) -> None:
    paths.base.mkdir(parents=True, exist_ok=True)
    if not force and paths.allowlist_file.exists():
        return
    try:
        text = TEMPLATE_ALLOWLIST.read_text()
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Allowlist template not found: {TEMPLATE_ALLOWLIST}\n"
            "Ensure you are running research.py from a complete checkout of the repo."
        ) from None
    paths.allowlist_file.write_text(text)
    verb = "Reseeded" if force else "Seeded"
    print(f"==> {verb} allowlist at {paths.allowlist_file}")


# ── Pure helpers ───────────────────────────────────────────────────────────────

def allowlist_to_regex_filter(lines: List[str]) -> str:
    """Convert allowlist lines to a tinyproxy filter file body.

    Each non-comment domain becomes an anchored regex that matches the domain
    itself and any subdomain: (^|\\.)example\\.com$
    """
    out: List[str] = []
    for raw in lines:
        domain = raw.split("#", 1)[0].strip()
        if not domain:
            continue
        out.append(f"(^|\\.){re.escape(domain)}$")
    return "\n".join(out) + "\n" if out else ""


def render_searxng_settings(bridge_ip: str, tinyproxy_port: int, secret: str) -> str:
    """Return the body of settings.yml for the research SearXNG instance."""
    return f"""\
use_default_settings:
  engines:
    keep_only:
      - google
      - bing
      - duckduckgo
      - brave
      - qwant
      - wikipedia
      - arxiv
      - google scholar
      - semantic scholar

server:
  secret_key: "{secret}"
  base_url: "http://research-searxng:8080/"
  limiter: false

search:
  formats:
    - html
    - json

outgoing:
  proxies:
    all://: "http://{bridge_ip}:{tinyproxy_port}"
"""


def render_tinyproxy_conf(bridge_ip: str, tinyproxy_port: int) -> str:
    """Return the body of tinyproxy.conf for the research VM."""
    return f"""\
User tinyproxy
Group tinyproxy
Port {tinyproxy_port}
Listen {bridge_ip}
Timeout 600
DefaultErrorFile "/usr/share/tinyproxy/default.html"
StatFile "/usr/share/tinyproxy/stats.html"
LogFile "/var/log/tinyproxy/tinyproxy.log"
LogLevel Info
MaxClients 100
FilterDefaultDeny Yes
Filter "/etc/tinyproxy/filter"
FilterExtended Yes
FilterURLs No
ConnectPort 443
ConnectPort 80
"""


def render_iptables_apply_script(
    bridge_ip: str,
    bridge_cidr: str,
    research_net_cidr: str,
    host_ip: str,
    tinyproxy_port: int,
    inference_port: int,
) -> str:
    """Return a shell script that applies the RESEARCH iptables chain.

    All variables are interpolated here at template-render time so the
    resulting shell script has no $VAR references — no nested escaping needed.
    """
    return f"""\
#!/bin/sh
set -e

# Ensure DOCKER-USER exists (docker creates it on fresh daemons).
iptables -N DOCKER-USER 2>/dev/null || true
iptables -C FORWARD -j DOCKER-USER 2>/dev/null || iptables -I FORWARD 1 -j DOCKER-USER

# Dedicated RESEARCH chain: create if absent, then flush to start clean.
iptables -N RESEARCH 2>/dev/null || true
iptables -F RESEARCH

# Jump into our chain from DOCKER-USER for bridge traffic (idempotent).
iptables -C DOCKER-USER -s {bridge_cidr} -j RESEARCH 2>/dev/null \\
  || iptables -I DOCKER-USER 1 -s {bridge_cidr} -j RESEARCH

# Also jump for user-defined research-net traffic.
iptables -C DOCKER-USER -s {research_net_cidr} -j RESEARCH 2>/dev/null \\
  || iptables -I DOCKER-USER 2 -s {research_net_cidr} -j RESEARCH

# Rules in order:
iptables -A RESEARCH -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
iptables -A RESEARCH -d {bridge_ip} -p tcp --dport {tinyproxy_port} -j RETURN
iptables -A RESEARCH -d {host_ip}   -p tcp --dport {inference_port} -j RETURN
iptables -A RESEARCH -d {bridge_ip} -p udp --dport 53 -j RETURN
iptables -A RESEARCH -d {bridge_ip} -p tcp --dport 53 -j RETURN
# Allow Vane → SearXNG on port 8080 within research-net.
iptables -A RESEARCH -s {research_net_cidr} -d {research_net_cidr} -p tcp --dport 8080 -j RETURN
iptables -A RESEARCH -j REJECT --reject-with icmp-admin-prohibited
"""


# ── Subprocess wrappers ────────────────────────────────────────────────────────

def _colima_profile() -> str:
    return COLIMA_PROFILE


def vm_sh(cmd: str, *, profile: str = COLIMA_PROFILE, check: bool = True) -> subprocess.CompletedProcess:
    """Run a raw shell command string inside the Colima VM via stdin pipe.

    Piping via stdin bypasses colima's argv-join quoting so shell pipelines,
    redirections, and heredocs work correctly on the remote side.
    """
    return subprocess.run(
        ["colima", "ssh", "-p", profile, "--", "bash"],
        input=cmd,
        text=True,
        capture_output=True,
        check=check,
    )


def vm_ssh(args: List[str], *, profile: str = COLIMA_PROFILE, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command (argv list) inside the Colima VM.

    Builds a quoted command string and pipes it via vm_sh to avoid
    colima's argv-join double-quoting issue.
    """
    import shlex
    cmd = " ".join(shlex.quote(a) for a in args)
    return vm_sh(cmd, profile=profile, check=check)


def vm_put_file(local_path: Path, remote_path: str, *, profile: str = COLIMA_PROFILE, mode: str = "644") -> None:
    """Copy a host-side file into the VM at remote_path via sudo tee."""
    content = local_path.read_bytes()
    subprocess.run(
        ["colima", "ssh", "-p", profile, "--", "sudo", "tee", remote_path],
        input=content,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["colima", "ssh", "-p", profile, "--", "sudo", "chmod", mode, remote_path],
        capture_output=True,
        check=True,
    )


def colima_profile_running(profile: str = COLIMA_PROFILE) -> bool:
    result = subprocess.run(
        ["colima", "list", "-p", profile, "--json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return False
    import json
    try:
        return json.loads(result.stdout.strip()).get("status") == "Running"
    except (json.JSONDecodeError, AttributeError):
        return False


def docker_container_exists(name: str) -> bool:
    result = subprocess.run(
        ["docker", "container", "inspect", name],
        capture_output=True,
    )
    return result.returncode == 0


def docker_network_exists(name: str) -> bool:
    result = subprocess.run(
        ["docker", "network", "inspect", name],
        capture_output=True,
    )
    return result.returncode == 0


# ── Phase functions ────────────────────────────────────────────────────────────

def ensure_colima_vm(config: VmConfig) -> None:
    if colima_profile_running(config.profile_name):
        # Warn if the running VM was sized differently.
        result = subprocess.run(
            ["colima", "list", "-p", config.profile_name, "--json"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            import json
            try:
                info = json.loads(result.stdout.strip())
                running_cpus = info.get("cpus", "")
                running_mem = info.get("memory", "")
                if running_cpus and str(running_cpus) != str(config.cpus):
                    print(
                        f"warning: running VM has {running_cpus} CPUs; "
                        f"requested {config.cpus}. Use --rebuild to resize.",
                        file=sys.stderr,
                    )
                if running_mem and str(config.memory_gib) not in str(running_mem):
                    print(
                        f"warning: running VM memory is {running_mem}; "
                        f"requested {config.memory_gib} GiB. Use --rebuild to resize.",
                        file=sys.stderr,
                    )
            except (json.JSONDecodeError, AttributeError):
                pass
        return

    print(
        f"==> Starting Colima VM '{config.profile_name}' "
        f"({config.memory_gib} GiB RAM, {config.cpus} CPUs)"
    )
    subprocess.run(
        [
            "colima", "start", "-p", config.profile_name,
            "--vm-type", "vz",
            "--runtime", "docker",
            "--cpu", str(config.cpus),
            "--memory", str(config.memory_gib),
            "--mount-type", "virtiofs",
            "--network-address",
        ],
        check=True,
    )


def discover_network(config: VmConfig) -> VmConfig:
    """Discover bridge IP, host IP, and CIDRs inside the VM; return updated config."""
    bridge_ip = vm_sh(
        "docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}'",
        check=False,
    ).stdout.strip().strip("\r")
    if not bridge_ip:
        bridge_ip = "172.17.0.1"
        print(f"warning: could not discover docker bridge gateway; falling back to {bridge_ip}", file=sys.stderr)

    bridge_cidr = vm_sh(
        "docker network inspect bridge -f '{{(index .IPAM.Config 0).Subnet}}'",
        check=False,
    ).stdout.strip().strip("\r")
    if not bridge_cidr:
        bridge_cidr = "172.17.0.0/16"

    host_ip = vm_sh(
        "ip route show default 2>/dev/null | awk '/^default/ {print $3; exit}'",
        check=False,
    ).stdout.strip().strip("\r")
    if not host_ip:
        for candidate in ("host.lima.internal", "host.docker.internal"):
            out = vm_sh(f"getent hosts {candidate} 2>/dev/null", check=False).stdout.strip()
            if out:
                host_ip = out.split()[0]
                break
    if not host_ip:
        print(
            f"warning: could not determine the macOS host IP from inside the VM; "
            f"local inference ({config.inference_label}) will not work.",
            file=sys.stderr,
        )
        host_ip = "127.0.0.1"

    print(f"==> VM network: bridge={bridge_ip} cidr={bridge_cidr} host={host_ip}")

    config.bridge_ip = bridge_ip
    config.bridge_cidr = bridge_cidr
    config.host_ip = host_ip
    return config


def ensure_docker_context(profile: str = COLIMA_PROFILE) -> None:
    result = subprocess.run(
        ["docker", "context", "use", f"colima-{profile}"],
        capture_output=True,
    )
    if result.returncode != 0:
        print(
            f"warning: could not switch docker context to colima-{profile}; "
            "assuming current context talks to the right daemon.",
            file=sys.stderr,
        )


def ensure_docker_network(config: VmConfig) -> None:
    """Create research-net if missing; populate config.research_net_cidr."""
    cidr = vm_sh(
        f"docker network inspect {RESEARCH_NET_NAME} -f '{{{{(index .IPAM.Config 0).Subnet}}}}' 2>/dev/null || true",
        check=False,
    ).stdout.strip().strip("\r")

    if not cidr:
        vm_sh(f"docker network create {RESEARCH_NET_NAME} >/dev/null")
        cidr = vm_sh(
            f"docker network inspect {RESEARCH_NET_NAME} -f '{{{{(index .IPAM.Config 0).Subnet}}}}' 2>/dev/null || true",
            check=False,
        ).stdout.strip().strip("\r")

    if not cidr:
        cidr = "172.20.0.0/24"
        print(f"warning: could not discover {RESEARCH_NET_NAME} CIDR; falling back to {cidr}", file=sys.stderr)

    config.research_net_cidr = cidr
    print(f"==> Research network: {RESEARCH_NET_NAME} cidr={cidr}")


def install_tinyproxy(config: VmConfig) -> None:
    result = vm_sh("command -v tinyproxy", check=False)
    if result.returncode != 0:
        print("==> Installing tinyproxy in Colima VM")
        vm_sh("sudo apt-get update -qq")
        vm_sh("sudo apt-get install -y tinyproxy")


def apply_firewall(config: VmConfig, paths: Paths) -> None:
    """Push tinyproxy config + filter into the VM, then apply iptables rules."""
    assert config.bridge_ip and config.bridge_cidr and config.host_ip and config.research_net_cidr

    allowlist_lines = paths.allowlist_file.read_text().splitlines()
    filter_body = allowlist_to_regex_filter(allowlist_lines)
    conf_body = render_tinyproxy_conf(config.bridge_ip, TINYPROXY_PORT)
    fw_script = render_iptables_apply_script(
        bridge_ip=config.bridge_ip,
        bridge_cidr=config.bridge_cidr,
        research_net_cidr=config.research_net_cidr,
        host_ip=config.host_ip,
        tinyproxy_port=TINYPROXY_PORT,
        inference_port=config.inference_port,
    )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        conf_file = tmp_path / "tinyproxy.conf"
        filter_file = tmp_path / "filter"
        fw_file = tmp_path / "firewall-apply.sh"

        conf_file.write_text(conf_body)
        filter_file.write_text(filter_body)
        fw_file.write_text(fw_script)

        vm_put_file(conf_file, "/etc/tinyproxy/tinyproxy.conf")
        vm_put_file(filter_file, "/etc/tinyproxy/filter")

        vm_sh("sudo systemctl enable --now tinyproxy >/dev/null 2>&1 || true")
        vm_sh("sudo systemctl restart tinyproxy")

        fw_content = fw_file.read_bytes()
        subprocess.run(
            ["colima", "ssh", "-p", config.profile_name, "--", "sudo", "sh"],
            input=fw_content,
            capture_output=True,
            check=True,
        )

    entry_count = sum(
        1 for line in allowlist_lines
        if line.split("#", 1)[0].strip()
    )
    print(f"==> Firewall applied ({entry_count} allowlist entries)")
    print(f"    proxy: http://{config.bridge_ip}:{TINYPROXY_PORT}")


def seed_searxng_settings(paths: Paths, config: VmConfig) -> None:
    paths.searxng_dir.mkdir(parents=True, exist_ok=True)
    if not paths.searxng_settings.exists():
        secret = secrets.token_hex(32)
        assert config.bridge_ip
        paths.searxng_settings.write_text(
            render_searxng_settings(config.bridge_ip, TINYPROXY_PORT, secret)
        )
        print(f"==> Seeded {paths.searxng_settings} (secret_key generated, proxy={config.bridge_ip}:{TINYPROXY_PORT})")
    else:
        # Drift check: fix stale proxy address if it doesn't match current bridge IP.
        assert config.bridge_ip
        expected_proxy = f"http://{config.bridge_ip}:{TINYPROXY_PORT}"
        content = paths.searxng_settings.read_text()
        m = re.search(r'all://:\s*"([^"]+)"', content)
        if m and m.group(1) != expected_proxy:
            print(f"==> SearXNG proxy drift: {m.group(1)} → {expected_proxy}")
            new_content = re.sub(
                r'(all://:\s*)"[^"]+"',
                f'\\1"{expected_proxy}"',
                content,
            )
            paths.searxng_settings.write_text(new_content)


def ensure_searxng_container(paths: Paths, config: VmConfig) -> bool:
    """Start or create the SearXNG container. Returns True if newly created."""
    print("==> Starting SearXNG container")
    if not docker_container_exists(CONTAINER_SEARXNG):
        subprocess.run(
            [
                "docker", "run", "-d",
                "--name", CONTAINER_SEARXNG,
                "--network", RESEARCH_NET_NAME,
                "-v", f"{paths.searxng_settings}:/etc/searxng/settings.yml:ro",
                "docker.io/searxng/searxng",
            ],
            capture_output=True,
            check=True,
        )
        print(f"    {CONTAINER_SEARXNG}: created")
        return True
    else:
        subprocess.run(
            ["docker", "start", CONTAINER_SEARXNG],
            capture_output=True,
        )
        print(f"    {CONTAINER_SEARXNG}: started (existing container)")
        return False


def ensure_vane_container(paths: Paths, config: VmConfig) -> None:
    """Start or create the Vane container."""
    paths.vane_data_dir.mkdir(parents=True, exist_ok=True)
    print("==> Starting Vane container")
    if not docker_container_exists(CONTAINER_VANE):
        subprocess.run(
            [
                "docker", "run", "-d",
                "--name", CONTAINER_VANE,
                "--network", RESEARCH_NET_NAME,
                "--add-host", "host.docker.internal:host-gateway",
                "-p", f"{config.vane_port}:3000",
                "-e", f"SEARXNG_API_URL=http://{CONTAINER_SEARXNG}:8080",
                "-v", f"{paths.vane_data_dir}:/home/vane/data",
                "docker.io/itzcrazykns1337/vane:slim-latest",
            ],
            capture_output=True,
            check=True,
        )
        print(f"    {CONTAINER_VANE}: created (http://localhost:{config.vane_port})")
        print(f"    note: configure LLM at http://localhost:{config.vane_port} on first access")
    else:
        subprocess.run(
            ["docker", "start", CONTAINER_VANE],
            capture_output=True,
        )
        print(f"    {CONTAINER_VANE}: started (existing container)")


def probe_inference(config: VmConfig) -> None:
    """Non-fatal probe to warn if the inference backend is not reachable."""
    assert config.host_ip
    print(f"==> Probing {config.inference_label} at http://{config.host_ip}:{config.inference_port} from inside VM")
    if config.backend == "ollama":
        result = vm_sh(
            f"curl -sf --max-time 3 http://{config.host_ip}:{config.inference_port}/api/tags",
            check=False,
        )
        if result.returncode != 0:
            print(
                f"warning: Ollama not reachable at http://{config.host_ip}:{config.inference_port} "
                f"from inside the Colima VM.\n"
                f"Ensure Ollama is running on the macOS host and bound to 0.0.0.0.\n"
                f"On the host, run once:\n"
                f"    launchctl setenv OLLAMA_HOST 0.0.0.0:{config.inference_port}\n"
                f"and restart the Ollama app. Continuing without local inference.",
                file=sys.stderr,
            )
    else:
        omlx_key = os.environ.get("OMLX_API_KEY", "")
        auth_header = f'-H "Authorization: Bearer {omlx_key}"' if omlx_key else ""
        result = vm_sh(
            f"curl -sf --max-time 3 {auth_header} http://{config.host_ip}:{config.inference_port}/v1/models",
            check=False,
        )
        if result.returncode != 0:
            print(
                f"warning: omlx not reachable at http://{config.host_ip}:{config.inference_port} "
                f"from inside the Colima VM.\n"
                f"Ensure omlx is running on the host. Continuing without local inference.",
                file=sys.stderr,
            )


def rebuild_teardown(config: VmConfig) -> None:
    """Remove containers (and optionally the Colima VM) before a rebuild."""
    for name in (CONTAINER_VANE, CONTAINER_SEARXNG):
        if docker_container_exists(name):
            print(f"==> --rebuild: removing container '{name}'")
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)

    print()
    answer = input(
        f"Also delete and recreate the Colima VM '{config.profile_name}'? "
        "This is NOT reversible. [y/N] "
    )
    if answer.strip().lower() in ("y", "yes"):
        print(f"==> Destroying Colima VM '{config.profile_name}'")
        subprocess.run(
            ["colima", "delete", "-p", config.profile_name, "--force"],
            capture_output=True,
        )
    else:
        print(f"==> Keeping Colima VM; only containers will be rebuilt.")


def reload_allowlist_fast_path(paths: Paths, config: VmConfig) -> None:
    """Regenerate tinyproxy filter and reload tinyproxy. No container restart."""
    assert config.bridge_ip and config.research_net_cidr

    allowlist_lines = paths.allowlist_file.read_text().splitlines()
    filter_body = allowlist_to_regex_filter(allowlist_lines)
    conf_body = render_tinyproxy_conf(config.bridge_ip, TINYPROXY_PORT)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        conf_file = tmp_path / "tinyproxy.conf"
        filter_file = tmp_path / "filter"
        conf_file.write_text(conf_body)
        filter_file.write_text(filter_body)
        vm_put_file(conf_file, "/etc/tinyproxy/tinyproxy.conf")
        vm_put_file(filter_file, "/etc/tinyproxy/filter")

    vm_sh(
        "sudo systemctl reload tinyproxy 2>/dev/null || sudo systemctl restart tinyproxy"
    )

    entry_count = sum(
        1 for line in allowlist_lines
        if line.split("#", 1)[0].strip()
    )
    print(f"==> Allowlist reloaded ({entry_count} entries)")


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    paths = Paths()
    config = VmConfig(
        memory_gib=args.memory,
        cpus=args.cpus,
        backend=args.backend,
        vane_port=args.vane_port,
    )

    # --reseed-allowlist: overwrite on-disk allowlist before any other operation.
    if args.reseed_allowlist:
        seed_allowlist(paths, force=True)

    # --reload-allowlist: need VM running + network discovered, then fast-exit.
    if args.reload_allowlist:
        if not colima_profile_running(config.profile_name):
            print(f"error: Colima VM '{config.profile_name}' is not running. Start it first.", file=sys.stderr)
            sys.exit(1)
        ensure_docker_context(config.profile_name)
        config = discover_network(config)
        ensure_docker_network(config)
        reload_allowlist_fast_path(paths, config)
        return

    # --rebuild: tear down containers (and optionally VM) before bring-up.
    if args.rebuild:
        if colima_profile_running(config.profile_name):
            ensure_docker_context(config.profile_name)
            rebuild_teardown(config)
        else:
            print(f"==> VM '{config.profile_name}' not running; nothing to remove.")

    # ── Full bring-up ──────────────────────────────────────────────────────────
    seed_allowlist(paths)

    ensure_colima_vm(config)
    ensure_docker_context(config.profile_name)

    config = discover_network(config)
    ensure_docker_network(config)

    install_tinyproxy(config)
    seed_searxng_settings(paths, config)
    apply_firewall(config, paths)

    probe_inference(config)

    ensure_searxng_container(paths, config)
    ensure_vane_container(paths, config)

    print()
    print("==> Research environment ready")
    print(f"    Vane    : http://localhost:{config.vane_port}")
    print( "    SearXNG : http://localhost:8080 (internal to VM)")
    print(f"    LLM     : configure at http://localhost:{config.vane_port} → Settings → LLM")
    if config.backend == "ollama":
        print(f"              use http://host.docker.internal:{config.inference_port} (Ollama)")
    else:
        print(f"              use http://host.docker.internal:{config.inference_port}/v1 (omlx)")
    print(f"    proxy   : http://{config.bridge_ip}:{TINYPROXY_PORT}  (allowlist: {paths.allowlist_file})")


if __name__ == "__main__":
    main()
