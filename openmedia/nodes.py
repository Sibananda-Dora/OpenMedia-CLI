import requests
from openmedia.state import AgentState

def generator_node(state: AgentState):
    """Generates the FFmpeg command using the local Ollama model."""
    print(f"[Brain] Thinking... (Attempt {state.iteration_count + 1})")
    
    prompt = f"""
    Target File: {state.target_file}
    User Request: {state.user_input}
    Last Error: {state.error_message if state.error_message else 'None'}
    
    Task: Output ONLY the raw FFmpeg command. Use NVIDIA hardware acceleration (nvenc) if applicable.
    """
    
    response = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": "qwen2.5-coder:7b-instruct-q4_K_M",
            "prompt": prompt,
            "stream": False
        }
    )
    
    command = response.json()['response'].strip().replace('`', '')
    
    # Update the Pydantic state
    state.generated_command = command
    state.iteration_count += 1
    return state

def validator_node(state: AgentState):
    """The safety firewall."""
    cmd = state.generated_command
    
    # Rule 1: No destructive commands
    if any(forbidden in cmd for forbidden in ["rm ", "del ", "format", "> /dev/"]):
        state.is_valid = False
        state.error_message = "Security breach: Destructive command detected."
    # Rule 2: Must be FFmpeg
    elif not cmd.startswith("ffmpeg"):
        state.is_valid = False
        state.error_message = "Invalid format: Command must start with 'ffmpeg'."
    else:
        state.is_valid = True
        state.error_message = None
        
    return state