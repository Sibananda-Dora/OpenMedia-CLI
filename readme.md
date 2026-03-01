# OpenMedia CLI

OpenMedia is a local CLI that converts natural-language media requests into FFmpeg commands using Ollama, validates them, and optionally executes them.

## Status

This project is still evolving. Safety and UX are actively being improved.

## Features

- Local command generation with Ollama (`qwen2.5-coder:7b-instruct-q4_K_M`)
- Programmatic command validation before execution
- Optional LLM safety review pass
- CPU/NVIDIA-aware execution tuning
- Deterministic fallback commands for common operations

## Requirements

- Python 3.10+
- FFmpeg and FFprobe in `PATH`
- Ollama installed and running

## Quick Start

```bash
git clone https://github.com/Sibananda-Dora/OpenMedia-CLI.git
cd OpenMedia-CLI
python install.py
```

## Run

Windows launcher:

```bat
start.bat
```

Direct CLI:

```bash
openmedia
```

Useful flags:

- `openmedia --dry-run`
- `openmedia --settings`
- `openmedia --ultra-safe`

## Testing

```bash
python -m unittest discover -s tests -v
```
## Contributions 
Feel free to contribute and PRs.
