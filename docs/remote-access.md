# Using one machine's GPU from another on your LAN

The setup: a desktop with the GPU serves inference; a laptop runs Claude Code
and OpenCode against it. Your files stay on the laptop and are edited there.
Only inference crosses the network.

That split matters. It's tempting to run everything on the desktop and remote
into it, but then the laptop isn't really doing the work and you've built a
worse remote desktop. Pointing OpenCode's `baseURL` at the desktop keeps your
editor, your repo, and your git history local, and sends only prompts and
completions over the wire.

## Understand what you are exposing

**Ollama has no authentication.** None. No password, no API key, no token.
Anyone who can reach the port can:

- run inference on your GPU, for as long as they like
- pull models, filling your disk
- **delete your models**, via the DELETE endpoint

So the port is the whole security boundary, and it must never be reachable
from the internet. Do not port-forward 11434 on your router. If you need
access from outside your LAN, use Tailscale or an SSH tunnel instead, so
there is no open port at all.

Recent Ollama versions bind `0.0.0.0` by default, which surprises people who
assume localhost. Check with `netstat -ano | findstr 11434`: if you see
`0.0.0.0:11434` rather than `127.0.0.1:11434`, the socket is already open to
the network and only the firewall is stopping traffic.

## Serving machine (the one with the GPU)

**1. Bind explicitly**, rather than relying on the default:

```powershell
[Environment]::SetEnvironmentVariable('OLLAMA_HOST','0.0.0.0:11434','User')
```

Restart Ollama afterwards. Setting it explicitly also documents the intent
for whoever reads the config later, including you in six months.

**2. Open the port to your LAN only.** Needs an elevated PowerShell. Replace
the subnet with your own:

```powershell
New-NetFirewallRule -DisplayName "Ollama LAN (inference)" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 11434 `
  -RemoteAddress 192.168.50.0/24 -Profile Any
```

`-RemoteAddress` is the important part. Without it the rule accepts traffic
from anywhere the interface can reach, which is a much larger surface than
you meant. Scoping to the subnet means a device would have to already be on
your network to reach it.

If Windows has the network marked `Public` (common on a home Ethernet where
network discovery was declined), `-Profile Any` keeps the rule working
without you having to reclassify the network. Reclassifying to `Private` is
also reasonable, but it loosens other defaults at the same time, so the
subnet-scoped rule is the tighter change.

**3. Confirm it is listening:**

```powershell
netstat -ano | findstr 11434
```

## Client machine (the laptop)

**Every model must be declared in `opencode.json`.** There is no discovery
step: OpenCode will not use a tag that isn't in its config, and asking for
one fails with an opaque `UnknownError` that never mentions the real cause.
Confirmed by removing a working model from the config and watching it break.

So generate the file rather than hand-writing it, especially on a second
machine where the model list has to stay in step with a GPU you aren't
sitting at:

```bash
python skills/foreman-recommend/scripts/gen_opencode_config.py \
  --host 192.168.50.100 --default-model qwen3.6:35b-a3b --write ./opencode.json
```

It reads the live model list from the serving machine and writes the whole
provider block with sane per-model limits. Re-run it after pulling anything
new. Without `--write` it prints to stdout so you can look first.

The generated `tool_call: true` on every model is a default, not a finding.
Ollama doesn't report tool-calling per tag, and a capability claim is not
evidence: one model in this project's testing advertised function calling and
scored 0/2. Verify before trusting, as always.

If you'd rather write it by hand, this is the shape:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "ollama/qwen3.6:35b-a3b",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (desktop GPU)",
      "options": { "baseURL": "http://192.168.50.100:11434/v1" },
      "models": {
        "qwen3.6:35b-a3b": {
          "name": "Qwen3.6 35B A3B (remote)",
          "tool_call": true,
          "limit": { "context": 32768, "output": 8192 },
          "cost": { "input": 0, "output": 0, "cache_read": 0, "cache_write": 0 }
        }
      }
    }
  }
}
```

Check it end to end from the laptop before trusting it:

```bash
curl http://192.168.50.100:11434/api/tags
```

A model list means you're through. A hang means the firewall rule is missing
or scoped to the wrong subnet; connection refused means Ollama isn't bound to
`0.0.0.0`.

Give the serving machine a DHCP reservation on your router. If its address
moves, every client config silently points at nothing.

## One config that works on both machines

The serving machine can reach Ollama through its own LAN address, not just
through `localhost`. So if you point *every* machine at the LAN IP, including
the one hosting the GPU, the config file becomes identical everywhere and you
can sync it without thinking about which machine you're on. Verified working
on the serving machine itself, going out to its own address rather than
looping back:

```json
"options": { "baseURL": "http://192.168.50.100:11434/v1" }
```

The trade is worth understanding before you take it. `localhost` cannot break
and does not depend on the network stack, a DHCP lease, or the firewall rule
staying in place. A LAN address can break in all of those ways, and then it
breaks on the serving machine too, which is the one place you'd expect to
still work. With a DHCP reservation that's a small risk against never
maintaining two configs.

Pick whichever failure you'd rather debug. If you keep them separate, the
serving machine uses `localhost` and only clients use the LAN address.

## What changes about the model choice

`foreman-recommend` reads VRAM from the machine it runs on. Run it on the
laptop against a remote Ollama and it will size recommendations to the
laptop's GPU, which is the wrong hardware and usually far too small.

Two ways to handle it:

- Run the shortlister **on the serving machine**, where its detection is
  correct, and copy the answer to the client.
- Or pass `--vram-gb N` on the client with the serving machine's VRAM.

The second is easy to forget, so prefer the first when you can.

## Expect it to be slower, and know why

Network latency is not the reason. Prompts and completions are small; a LAN
round trip is milliseconds against inference measured in seconds.

The real cost is that model loading is not shared. Ollama unloads after about
five minutes idle, so a laptop session that pauses will pay the reload cost
again, and a 23GB model takes a while to come back. If several people use the
machine, they contend for the same GPU: with `OLLAMA_NUM_PARALLEL=1` set (and
it should be, see `opencode-setup.md`) requests queue rather than running
together.

Raising `OLLAMA_KEEP_ALIVE` on the serving machine reduces reload churn at the
cost of holding VRAM continuously. That is a fair trade on a machine whose job
is serving models, and a bad one on a machine you also game on.
