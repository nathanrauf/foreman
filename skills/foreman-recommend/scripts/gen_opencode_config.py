#!/usr/bin/env python3
"""Generate an opencode.json provider block from a running Ollama instance.

OpenCode will not use a model that isn't declared in its config. Ask for an
undeclared tag and it fails with an opaque "UnknownError" that says nothing
about the real cause, so the models map has to be written by hand and kept in
step with whatever is actually pulled. This reads the live model list and
writes that block for you.

Mainly worth it when pointing a second machine at a shared GPU: run it with
--host on the laptop and it produces a config aimed at the desktop, with the
context window and capability flags already set per model.

Usage:
    python gen_opencode_config.py                        # local Ollama
    python gen_opencode_config.py --host 192.168.50.100  # remote
    python gen_opencode_config.py --host 192.168.50.100 --write ./opencode.json

Without --write it prints to stdout, so you can look before overwriting
anything.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

# Context is capped rather than taken from the model's maximum. Ollama sizes
# its KV cache from the requested window, so asking for a model's full 256K
# on a card that cannot hold it pushes weights onto the CPU and slows
# everything down. 32768 comfortably fits a real coding task; raise it per
# model if a task genuinely needs more.
DEFAULT_CONTEXT = 32768
DEFAULT_OUTPUT = 8192

FREE = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def fetch_models(base):
    url = f"{base}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read()).get("models", [])
    except urllib.error.URLError as e:
        sys.exit(
            f"error: could not reach Ollama at {url} ({e}).\n"
            "If the host is remote, check that it is running, that OLLAMA_HOST\n"
            "is 0.0.0.0, and that the firewall allows your subnet on 11434.\n"
            "See docs/remote-access.md."
        )


def model_entry(info, context, where):
    name = info["name"]
    details = info.get("details") or {}
    families = [f.lower() for f in (details.get("families") or [])]
    # Ollama does not report tool-calling per tag, so this is declared true and
    # left to empirical testing to disprove. A capability claim is not
    # evidence: one model here advertised function calling and scored 0/2.
    entry = {
        "name": f"{name} ({where})",
        "tool_call": True,
        "temperature": True,
        "limit": {"context": context, "output": DEFAULT_OUTPUT},
        "cost": dict(FREE),
    }
    if any("clip" in f or "vision" in f for f in families):
        entry["attachment"] = True
    return name, entry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost",
                    help="Host serving Ollama (default: localhost)")
    ap.add_argument("--port", type=int, default=11434)
    ap.add_argument("--default-model",
                    help="Tag to set as opencode's default model")
    ap.add_argument("--context", type=int, default=DEFAULT_CONTEXT)
    ap.add_argument("--write", metavar="PATH",
                    help="Write to PATH instead of printing")
    args = ap.parse_args()

    base = f"http://{args.host}:{args.port}"
    models = fetch_models(base)
    if not models:
        sys.exit(f"error: {base} reports no models. Pull one first.")

    where = "local" if args.host in ("localhost", "127.0.0.1") else f"on {args.host}"
    entries = dict(model_entry(m, args.context, where) for m in models)

    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "ollama": {
                "npm": "@ai-sdk/openai-compatible",
                "name": f"Ollama ({args.host})",
                "options": {"baseURL": f"{base}/v1"},
                "models": entries,
            }
        },
    }

    default = args.default_model
    if default and default not in entries:
        sys.exit(f"error: --default-model {default!r} is not on {args.host}. "
                 f"Available: {', '.join(entries)}")
    if default:
        config["model"] = f"ollama/{default}"

    text = json.dumps(config, indent=2) + "\n"
    if args.write:
        with open(args.write, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {args.write} with {len(entries)} model(s) from {base}")
        for tag in entries:
            print(f"  - {tag}")
        if not default:
            print("\nNo default set. Pass --default-model <tag>, or add a "
                  "top-level \"model\" key yourself.")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
