# local-head
Claude Code skills that hand coding and research work to local Ollama models, so your GPU does the token-heavy part instead of your Claude usage.

Claude will handle the planning and judgment for tasks while a local model on your own GPU does the bulk work: summarizing large files, drafting boilerplate, or running a multi-step coding task on its own while Claude reviews at checkpoints.
Everything here was tested on a real machine (RTX 5070 Ti, 16GB VRAM, Windows 11), including the parts that broke. The findings section covers the failures too, because most of them are not obvious and cost real debugging time.
