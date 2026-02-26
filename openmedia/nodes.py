import os
import requests
import shlex
from openmedia.state import AgentState

FORBIDDEN_TOKENS = {
    "rm",
    "del",
    "erase",
    "powershell",
    "cmd",
    "bash",
    "sh",
    "python",
    "curl",
    "wget",
    "invoke-webrequest",
}
SHELL_OPERATORS = {"|", "||", "&&", ";", "&"}
REDIRECTION_PREFIXES = (">", "<", "1>", "2>", ">>")

def _get_executable_name(token):
    cleaned = token.strip("'\"").replace("\\", "/")
    return cleaned.rsplit("/", 1)[-1].lower()

def _tokenize_command(command):
    return shlex.split(command, posix=False)

def generator_node(state: AgentState):
    """Generates the FFmpeg command using the local Ollama model."""
    print(f"[Brain] Thinking... (Attempt {state.iteration_count + 1})")
    
    prompt = f"""
    Target File: {state.target_file}
    Media Info: {state.media_context if state.media_context else 'Unavailable'}
    User Request: {state.user_input}
    Last Error: {state.error_message if state.error_message else 'None'}
    Ultra Safe Mode: {state.ultra_safe}
    Encoding Preference: {state.encoding_preference if state.encoding_preference else 'auto'}
    LLM Runtime Preference: {state.llm_runtime_mode if state.llm_runtime_mode else 'auto'}
    LLM Effective Runtime: {state.llm_effective_mode if state.llm_effective_mode else 'cpu'}
    Effective Encoder Family: {state.effective_encoder_family if state.effective_encoder_family else 'cpu'}
    Preferred Video Encoder: {state.preferred_video_encoder if state.preferred_video_encoder else 'libx264'}
    NVENC Available: {state.nvenc_available if state.nvenc_available is not None else 'unknown'}
    
    Task: Output ONLY one raw FFmpeg command.
    Rules:
    - Start with ffmpeg.
    - Do not include shell chaining, redirection, scripts, or non-ffmpeg utilities.
    - If effective encoder family is 'nvidia', use h264_nvenc or hevc_nvenc for video.
    - In nvidia mode, prefer -cq for quality control and avoid x264-only presets like veryfast/superfast.
    - If effective encoder family is 'cpu', use libx264 for video with a moderate preset (prefer veryfast).
    - Avoid very CPU-heavy presets such as slow, slower, veryslow, or placebo.
    - If ultra safe mode is true, prefer lower-resource settings and avoid aggressive quality settings.
    """

    try:
        ollama_options = {}
        if (state.llm_effective_mode or "").lower() == "cpu":
            ollama_options["num_gpu"] = 0
            cpu_threads = os.cpu_count() or 2
            thread_ratio = 0.4 if state.ultra_safe else 0.7
            ollama_options["num_thread"] = max(1, int(cpu_threads * thread_ratio))
            request_timeout = 180 if state.ultra_safe else 120
        else:
            request_timeout = 60 if state.ultra_safe else 45

        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "qwen2.5-coder:7b-instruct-q4_K_M",
                "prompt": prompt,
                "stream": False,
                "keep_alive": 0,
                "options": ollama_options
            },
            timeout=request_timeout
        )
        response.raise_for_status()
        command = response.json()["response"].strip().replace("`", "")
        state.generated_command = command
    except Exception as exc:
        state.generated_command = None
        state.is_valid = False
        state.error_message = f"Generation failed: {exc}"

    state.iteration_count += 1
    return state

def validator_node(state: AgentState):
    """The safety firewall."""
    cmd = state.generated_command or ""

    if not cmd.strip():
        state.is_valid = False
        if not state.error_message:
            state.error_message = "Empty command generated."
        return state

    if "\n" in cmd or "\r" in cmd or "$(" in cmd or "`" in cmd:
        state.is_valid = False
        state.error_message = "Unsafe format: command contains forbidden shell syntax."
        return state

    try:
        tokens = _tokenize_command(cmd)
    except ValueError as exc:
        state.is_valid = False
        state.error_message = f"Unable to parse command safely: {exc}"
        return state

    if not tokens:
        state.is_valid = False
        state.error_message = "Empty tokenized command."
        return state

    executable = _get_executable_name(tokens[0])
    if executable not in {"ffmpeg", "ffmpeg.exe"}:
        state.is_valid = False
        state.error_message = "Invalid format: command must start with ffmpeg."
        return state

    lowered_tokens = [token.strip("'\"").lower() for token in tokens[1:]]

    for token in lowered_tokens:
        if token in SHELL_OPERATORS:
            state.is_valid = False
            state.error_message = "Unsafe command: shell chaining is not allowed."
            return state
        if token in FORBIDDEN_TOKENS:
            state.is_valid = False
            state.error_message = f"Unsafe command: forbidden token '{token}' detected."
            return state
        if token.startswith(REDIRECTION_PREFIXES) or "&&" in token or "||" in token:
            state.is_valid = False
            state.error_message = "Unsafe command: shell redirection/chaining detected."
            return state

    if "-i" not in lowered_tokens:
        state.is_valid = False
        state.error_message = "Invalid FFmpeg command: missing '-i' input."
        return state

    encoder_family = (state.effective_encoder_family or "").lower()
    if encoder_family == "cpu" and any("nvenc" in token for token in lowered_tokens):
        state.is_valid = False
        state.error_message = "Encoder policy violation: NVENC is not allowed in CPU mode."
        return state
    if encoder_family == "nvidia" and any(token in {"libx264", "libx265"} for token in lowered_tokens):
        state.is_valid = False
        state.error_message = "Encoder policy violation: software x264/x265 is not allowed in NVIDIA mode."
        return state
    if encoder_family == "nvidia" and not any("nvenc" in token for token in lowered_tokens):
        state.is_valid = False
        state.error_message = "Encoder policy violation: NVIDIA mode requires an NVENC video encoder."
        return state
    if encoder_family == "nvidia" and "-crf" in lowered_tokens:
        state.is_valid = False
        state.error_message = "Encoder policy violation: use '-cq' instead of '-crf' in NVIDIA mode."
        return state
    if encoder_family == "nvidia" and "-preset" in lowered_tokens:
        preset_index = lowered_tokens.index("-preset")
        if preset_index + 1 < len(lowered_tokens):
            preset_value = lowered_tokens[preset_index + 1]
            if preset_value in {"ultrafast", "superfast", "veryfast", "faster"}:
                state.is_valid = False
                state.error_message = "Encoder policy violation: selected preset is not compatible with NVENC."
                return state
    if encoder_family == "cpu" and "-preset" in lowered_tokens:
        preset_index = lowered_tokens.index("-preset")
        if preset_index + 1 < len(lowered_tokens):
            preset_value = lowered_tokens[preset_index + 1]
            if preset_value in {"slow", "slower", "veryslow", "placebo"}:
                state.is_valid = False
                state.error_message = "Encoder policy violation: selected CPU preset is too heavy."
                return state

    state.is_valid = True
    state.error_message = None
    return state

def safety_reviewer_node(state: AgentState):
    """A second-pass safety check using the LLM to verify malicious intent."""
    if not state.is_valid or not state.generated_command:
        return state

    print(f"[Safety Review] Auditing command for hidden risks...")
    
    audit_prompt = f"""
    You are a Senior Security Engineer and FFmpeg expert.
    
    User Intent: {state.user_input}
    Generated Command: {state.generated_command}
    Media Context: {state.media_context}
    Effective Encoder: {state.effective_encoder_family}
    
    Analyze the command for:
    1. Malicious intent (hidden shell escapes, file deletions, system calls).
    2. Side effects (overwriting important files outside of the output name).
    3. Technical correctness (does it actually do what the user asked?).
    
    Rules:
    - If it is 100% safe and correct, output ONLY 'APPROVED'.
    - If it is dangerous or incorrect, output 'REJECTED: [reason]'.
    """

    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "qwen2.5-coder:7b-instruct-q4_K_M",
                "prompt": audit_prompt,
                "stream": False,
                "keep_alive": 0,
            },
            timeout=45
        )
        response.raise_for_status()
        review = response.json()["response"].strip()
        
        if review.startswith("APPROVED"):
            state.is_valid = True
        else:
            state.is_valid = False
            state.error_message = f"Security Audit Failed: {review}"
    except Exception as exc:
        state.is_valid = False
        state.error_message = f"Safety Audit unreachable: {exc}"

    return state
