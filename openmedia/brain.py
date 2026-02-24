import requests

# You can easily swap this to qwen3-vl or llama3.2 later
MODEL = "qwen2.5-coder:7b-instruct-q4_K_M" 

SYSTEM_PROMPT = """
You are an expert FFmpeg assistant. 
1. Output ONLY the raw ffmpeg command. No explanations. No markdown blocks.
2. ALWAYS prefer 'h264_nvenc' or 'hevc_nvenc' for video encoding to utilize hardware acceleration.
3. Use 'output_file.mp4' as a placeholder if no output name is provided.
"""

def generate_command(user_query, target_file):
    """Sends the context to the local LLM and retrieves the command."""
    context = f"Target File: {target_file}\nUser Request: {user_query}"
    
    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": MODEL, 
                "prompt": f"{SYSTEM_PROMPT}\n{context}", 
                "stream": False,
                "keep_alive": 0  # Frees up VRAM immediately
            }
        )
        # Strip out any backticks the AI might accidentally include
        return response.json()['response'].strip().replace('`', '')
    except Exception as e:
        return f"Error: {e}"