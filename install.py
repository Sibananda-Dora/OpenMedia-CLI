import subprocess
import sys
import os

def run_command(command, description):
    print(f"[#] {description}...")
    try:
        subprocess.run(command, shell=True, check=True)
    except subprocess.CalledProcessError:
        print(f"[!] Error during: {description}")
        sys.exit(1)

def main():
    print("--- MediaHandle: First-Time Setup ---")
    
    # 1. Create Virtual Environment
    if not os.path.exists("venv"):
        run_command("python -m venv venv", "Creating virtual environment")
    
    # 2. Install Dependencies
    # Note: We use the venv's pip specifically
    pip_path = os.path.join("venv", "Scripts", "pip") if os.name == "nt" else "./venv/bin/pip"
    run_command(f"{pip_path} install -e .", "Installing project and dependencies (Rich, LangGraph, Pydantic)")
    
    # 3. Pull the Ollama Model
    print("[#] Checking for Qwen model in Ollama...")
    run_command("ollama pull qwen2.5-coder:7b-instruct-q4_K_M", "Pulling AI model (This may take a few minutes)")

    print("\n[✔] Setup Complete! You can now use the launcher to start OpenMedia.")

if __name__ == "__main__":
    main()