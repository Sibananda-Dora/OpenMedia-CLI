# 🎬 OpenMedia CLI (Local Edition)

> **Translate human intent into high-performance FFmpeg commands using Local Models.**

OpenMedia is an agentic CLI tool that uses a local **Qwen 2.5 Coder 7B** model to turn English requests into optimized, hardware-accelerated video processing commands. It is designed for developers and creators who want the power of FFmpeg without memorizing complex syntax.

---

## Quick Start (Automated)

### 1️⃣ Prerequisites
* **Python 3.10+** installed.
* **FFmpeg** installed and added to your System PATH.
* **[Ollama](https://ollama.com)** installed and running.

### 2️⃣ Installation
First, clone the repository and run the automated installer. This script will create a virtual environment, install dependencies, and download the AI model.

```bash
git clone [https://github.com/Sibananda-Dora/OpenMedia-Local.git](https://github.com/Sibananda-Dora/OpenMedia-Local.git)
cd openmedia
python install.py