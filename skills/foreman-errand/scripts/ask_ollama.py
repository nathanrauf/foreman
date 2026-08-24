#!/usr/bin/env python3
"""Send one self-contained prompt to a local Ollama model and print the reply.

No conversation history is kept; every call is stateless, so the prompt
must contain everything the model needs (the local model cannot see the
Claude Code conversation this is being called from).

Usage:
    python ask_ollama.py --model gpt-oss:20b --effort low --file prompt.txt
    echo "summarize this" | python ask_ollama.py --model gpt-oss:20b
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

OLLAMA_URL = "http://localhost:11434/api/chat"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--system", default="", help="Extra system instructions")
    parser.add_argument("--effort", choices=["low", "medium", "high"], default="low",
                         help="gpt-oss reasoning effort; ignored by models that don't support it")
    parser.add_argument("--file", help="Read the prompt from this file instead of stdin")
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--max-input-tokens", type=int, default=28000,
                         help="Refuse rather than silently send oversized input; a local model "
                              "given a prompt beyond its context window doesn't error, it just "
                              "produces a confused/generic response with no indication anything "
                              "went wrong. Raise this (and --num-ctx, VRAM permitting) if a "
                              "larger single call is genuinely wanted; otherwise pre-filter the "
                              "content (grep for the relevant lines) instead of sending everything.")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            prompt = f.read()
    else:
        prompt = sys.stdin.read()

    if not prompt.strip():
        print("error: empty prompt", file=sys.stderr)
        sys.exit(1)

    estimated_tokens = len(prompt) // 4
    if estimated_tokens > args.max_input_tokens:
        print(
            f"error: prompt is ~{estimated_tokens} estimated tokens, over the "
            f"{args.max_input_tokens}-token safety cap. Sending this as-is would silently "
            f"overflow the context window (currently num_ctx={args.num_ctx}) and produce a "
            f"confused/generic reply with no error. Pre-filter the content first (e.g. grep "
            f"for the relevant lines before summarizing) rather than raising the cap as a first "
            f"resort.",
            file=sys.stderr,
        )
        sys.exit(4)

    system = args.system
    if "gpt-oss" in args.model:
        system = f"Reasoning: {args.effort}\n{system}".strip()

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": args.model,
        "messages": messages,
        "stream": False,
        "options": {"num_ctx": args.num_ctx},
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError as e:
        print(f"error: could not reach Ollama at {OLLAMA_URL} ({e}). "
              f"Is `ollama serve` running and is `{args.model}` pulled?", file=sys.stderr)
        sys.exit(2)

    if "error" in data:
        print(f"error: {data['error']}", file=sys.stderr)
        sys.exit(3)

    content = data.get("message", {}).get("content", "")
    if not content.strip():
        print(
            "error: model returned empty content. This happens when the input is close enough "
            "to the context limit that the model spends its whole response budget on internal "
            "reasoning and never emits a final answer, not a crash, just silent non-output. "
            "Try again with a larger --num-ctx, a shorter prompt, or --effort low if not already "
            "set.",
            file=sys.stderr,
        )
        sys.exit(5)

    print(content)


if __name__ == "__main__":
    main()
