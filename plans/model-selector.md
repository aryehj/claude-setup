# Automated Local Model Selection for OpenCode

This plan describes an enhancement to `start-agent.sh` to automatically select a local LLM for `opencode` when the container is launched.

## Goal
Ensure that `opencode` is launched with a local model pre-selected if any are discovered on the host's Ollama or oMLX instance, avoiding the need for the user to manually select a model via `/model` on first run.

## Implementation Details

### 1. Update Python Injection Logic in `start-agent.sh`
Modify the Python script that generates `opencode.json` (lines 656-720) to:
- Capture the dictionary returned by `discover_models()`.
- If the dictionary is not empty, select the first available model name.
- Set a top-level `model` key in the `opencode.json` configuration with the value formatted as `{backend}:{model_name}` (e.g., `ollama:llama3`).

### 2. Verification
- The `models` dictionary for the provider will still be populated, ensuring that the user can still switch between all discovered models using the `/model` command in the OpenCode TUI.
- Confirm that `opencode.json` contains the `model` key after running `start-agent.sh`.
