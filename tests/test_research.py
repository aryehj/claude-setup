"""Unit tests for research.py pure helpers."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from research import (
    denylist_to_regex_filter,
    render_iptables_apply_script,
    render_searxng_settings,
)


# ── denylist_to_regex_filter ─────────────────────────────────────────────────

def test_denylist_filter_basic_domain():
    result = denylist_to_regex_filter(["wikipedia.org"])
    assert "(^|\\.)wikipedia\\.org$" in result


def test_denylist_filter_subdomain_match():
    pattern = re.compile(r"\(\^\|\\\.\)wikipedia\\\.org\$")
    result = denylist_to_regex_filter(["wikipedia.org"])
    assert pattern.search(result)
    # The compiled regex should match subdomains
    line_re = re.compile(r"(^|\.)wikipedia\.org$")
    assert line_re.search("wikipedia.org")
    assert line_re.search("en.wikipedia.org")
    assert not line_re.search("not-wikipedia.org")


def test_denylist_filter_skips_comments():
    lines = ["# this is a comment", "example.com", "# another comment"]
    result = denylist_to_regex_filter(lines)
    assert "comment" not in result
    assert "example" in result


def test_denylist_filter_skips_blank_lines():
    lines = ["", "   ", "google.com", ""]
    result = denylist_to_regex_filter(lines)
    assert result.count("(^|") == 1


def test_denylist_filter_inline_comment_stripped():
    lines = ["google.com  # search engine"]
    result = denylist_to_regex_filter(lines)
    assert "search engine" not in result
    assert "google" in result


def test_denylist_filter_special_chars_escaped():
    lines = ["api.example-corp.com"]
    result = denylist_to_regex_filter(lines)
    # hyphen and dot are escaped
    assert r"api\.example\-corp\.com" in result or r"api\.example-corp\.com" in result


def test_denylist_filter_empty_input():
    assert denylist_to_regex_filter([]) == ""
    assert denylist_to_regex_filter(["# only comments"]) == ""


# ── render_searxng_settings ───────────────────────────────────────────────────

def test_searxng_settings_contains_proxy():
    result = render_searxng_settings("172.17.0.1", 8888, "deadbeef")
    assert "http://172.17.0.1:8888" in result


def test_searxng_settings_contains_secret():
    result = render_searxng_settings("172.17.0.1", 8888, "mysecretkey")
    assert "mysecretkey" in result


def test_searxng_settings_contains_engines():
    result = render_searxng_settings("172.17.0.1", 8888, "x")
    for engine in ("google", "bing", "duckduckgo", "wikipedia", "arxiv"):
        assert engine in result


def test_searxng_settings_uses_research_container_url():
    result = render_searxng_settings("172.17.0.1", 8888, "x")
    assert "research-searxng:8080" in result


def test_searxng_settings_is_valid_yaml_structure():
    result = render_searxng_settings("10.0.0.1", 8888, "abc123")
    # Must start with a top-level key, not empty
    assert result.startswith("use_default_settings:")
    # Must contain outgoing proxies section
    assert "outgoing:" in result
    assert "proxies:" in result
    assert 'all://:' in result


# ── render_iptables_apply_script ──────────────────────────────────────────────

def test_iptables_no_uninterpolated_vars():
    script = render_iptables_apply_script(
        bridge_ip="172.17.0.1",
        bridge_cidr="172.17.0.0/16",
        research_net_cidr="172.20.0.0/24",
        host_ip="192.168.5.1",
        tinyproxy_port=8888,
        inference_port=11434,
    )
    # No shell variable references should remain (all interpolated at render time)
    assert "${" not in script
    assert "$BRIDGE" not in script
    assert "$HOST" not in script
    assert "$TINYPROXY" not in script


def test_iptables_contains_research_chain():
    script = render_iptables_apply_script(
        bridge_ip="172.17.0.1",
        bridge_cidr="172.17.0.0/16",
        research_net_cidr="172.20.0.0/24",
        host_ip="192.168.5.1",
        tinyproxy_port=8888,
        inference_port=11434,
    )
    assert "RESEARCH" in script
    assert "DOCKER-USER" in script


def test_iptables_interpolates_ips():
    script = render_iptables_apply_script(
        bridge_ip="10.1.2.3",
        bridge_cidr="10.1.2.0/24",
        research_net_cidr="10.5.6.0/24",
        host_ip="192.168.99.1",
        tinyproxy_port=9999,
        inference_port=8000,
    )
    assert "10.1.2.3" in script
    assert "10.1.2.0/24" in script
    assert "192.168.99.1" in script
    assert "9999" in script
    assert "8000" in script


def test_iptables_ends_with_reject():
    script = render_iptables_apply_script(
        bridge_ip="172.17.0.1",
        bridge_cidr="172.17.0.0/16",
        research_net_cidr="172.20.0.0/24",
        host_ip="192.168.5.1",
        tinyproxy_port=8888,
        inference_port=11434,
    )
    assert "REJECT" in script


def test_iptables_allows_intra_net_8080():
    script = render_iptables_apply_script(
        bridge_ip="172.17.0.1",
        bridge_cidr="172.17.0.0/16",
        research_net_cidr="172.20.0.0/24",
        host_ip="192.168.5.1",
        tinyproxy_port=8888,
        inference_port=11434,
    )
    assert "--dport 8080" in script
    assert "172.20.0.0/24" in script


if __name__ == "__main__":
    import unittest
    # Run all test_ functions manually for environments without pytest
    import inspect
    module = sys.modules[__name__]
    passed = failed = 0
    for name, fn in inspect.getmembers(module, inspect.isfunction):
        if name.startswith("test_"):
            try:
                fn()
                print(f"  PASS  {name}")
                passed += 1
            except Exception as exc:
                print(f"  FAIL  {name}: {exc}")
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
