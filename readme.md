# DON'T USE IT YET, YOU CAN CHECK THE CODEBASE BUT IT IS NOT MY PROBLEM IF YOU MODEL HALLUCINATES AND RAN A DANGEROUS CODE IN THE SHELL. WAIT TILL I ADD MORE SECURITY LOGIC.


# 🎬 OpenMedia CLI (Local Edition)

> **Translate human intent into high-performance FFmpeg commands using Local Models.**

##  Important: Read Before Use

###  Performance & Encoding Logic
This CLI is optimized for **NVIDIA GPUs** using the `h264_nvenc` and `hevc_nvenc` encoders. 
* **If you have an NVIDIA GPU (RTX 3050+):** The AI will prioritize hardware acceleration for near-instant processing.
* **If you do NOT have an NVIDIA GPU:** The AI will fallback to **Software Encoding** (`libx264` / `libx265`). 
  * *Note:* Software encoding is significantly more CPU-intensive and will be slower, but it produces higher quality/smaller file sizes.

### Minimum System Requirements
To run the local **7B AI Model** and **FFmpeg** simultaneously, your system should meet these specs:
* **OS:** Windows 10/11 or Linux.
* **CPU:** 4-Core (Intel i5 11th Gen / Ryzen 5 5000 series or better recommended).
* **RAM:** **16GB Minimum** 
* **GPU:** NVIDIA RTX 30-series or 40-series (4GB VRAM) for Hardware Acceleration.
* **Software:** [Ollama](https://ollama.com) must be installed and running.

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
git clone https://github.com/Sibananda-Dora/OpenMedia-CLI.git
cd OpenMedia-CLI
python install.py