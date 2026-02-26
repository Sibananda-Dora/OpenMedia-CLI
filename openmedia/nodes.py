import os
import requests
import shlex
import datetime
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
VIDEO_OUTPUT_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".ts",
    ".m2ts",
    ".m4v",
    ".flv",
    ".mpeg",
    ".mpg",
    ".wmv",
    ".3gp",
}
NON_VIDEO_OUTPUT_EXTENSIONS = {
    ".gif",
    ".webp",
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".mp3",
    ".wav",
    ".aac",
    ".flac",
    ".ogg",
    ".m4a",
}

def _get_executable_name(token):
    cleaned = token.strip("'\"").replace("\\", "/")
    return cleaned.rsplit("/", 1)[-1].lower()

def _tokenize_command(command):
    tokens = shlex.split(command, posix=False)
    normalized = []
    for token in tokens:
        if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
            normalized.append(token[1:-1])
        else:
            normalized.append(token)
    return normalized

def _has_unquoted_shell_metacharacters(command):
    in_single = False
    in_double = False

    for ch in command:
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if not in_single and not in_double and ch in {"&", ";", "|", "<", ">"}:
            return True
    return False

def _infer_output_extension(tokens):
    for token in reversed(tokens[1:]):
        cleaned = token.strip("'\"")
        if not cleaned or cleaned.startswith("-"):
            continue
        _, ext = os.path.splitext(cleaned)
        if ext:
            return ext.lower()
    return ""

def _is_palette_file(token):
    cleaned = token.strip("'\"").lower().replace("\\", "/")
    return cleaned.endswith(("/palette.png", "/palette.jpg", "/palette.jpeg", "/palette.bmp", "/palette.webp")) or cleaned in {
        "palette.png",
        "palette.jpg",
        "palette.jpeg",
        "palette.bmp",
        "palette.webp",
    }

def _log_generation(user_input, command, iteration, status):
    """Logs command generation attempts to generation.log for debugging."""
    log_file = "generation.log"
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    log_entry = (
        f"[{timestamp}] ATTEMPT: {iteration + 1} | STATUS: {status}\n"
        f"USER INPUT: {user_input}\n"
        f"GENERATED: {command}\n"
        f"{'-'*80}\n"
    )
    
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(log_entry)
    except Exception:
        pass  # Don't fail if logging fails

def generator_node(state: AgentState):
    """Generates the FFmpeg command using the local Ollama model."""
    print(f"[Brain] Thinking... (Attempt {state.iteration_count + 1})")
    
    prompt = f"""Target File: {state.target_file}
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

Task: Output ONLY one raw FFmpeg command on a single line.
Rules:
- Start with ffmpeg.
- Output must be a single line with no newlines or line breaks.
- Output exactly one ffmpeg invocation (never chain multiple ffmpeg commands).
- Do not include shell chaining, redirection, scripts, or non-ffmpeg utilities.
- Do not wrap the command in markdown code blocks or backticks.
- If effective encoder family is 'nvidia', use h264_nvenc or hevc_nvenc for video.
- For GIF/image outputs, do not use NVENC-only flags (-cq/-crf with nvenc); prefer a single ffmpeg filter_complex pipeline.
- For GIF outputs, use one input and one command; if you use palettegen you must also use paletteuse in the same filter graph.
- In nvidia mode, prefer -cq for quality control and avoid x264-only presets like veryfast/superfast.
- If effective encoder family is 'cpu', use libx264 for video with a moderate preset (prefer veryfast).
- Avoid very CPU-heavy presets such as slow, slower, veryslow, or placebo.
- If ultra safe mode is true, prefer lower-resource settings and avoid aggressive quality settings."""

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
        raw_command = response.json()["response"].strip()
        
        # Log the raw LLM output for debugging
        _log_generation(state.user_input, raw_command, state.iteration_count, "RAW")
        
        # Clean up common LLM formatting issues
        command = raw_command.replace("`", "")  # Remove markdown backticks
        command = command.replace("\n", " ")  # Remove newlines
        command = command.replace("\r", " ")  # Remove carriage returns
        command = " ".join(command.split())  # Normalize whitespace
        
        # Log the cleaned command
        if command != raw_command:
            _log_generation(state.user_input, command, state.iteration_count, "CLEANED")
        
        state.generated_command = command
    except Exception as exc:
        state.generated_command = None
        state.is_valid = False
        state.error_message = f"Generation failed: {exc}"
        _log_generation(state.user_input, f"ERROR: {exc}", state.iteration_count, "FAILED")

    state.iteration_count += 1
    return state

def validator_node(state: AgentState):
    """The safety firewall."""
    cmd = state.generated_command or ""

    if not cmd.strip():
        state.is_valid = False
        if not state.error_message:
            state.error_message = "Empty command generated."
        _log_validation(state.user_input, cmd, "REJECTED: Empty command")
        return state

    if "\n" in cmd or "\r" in cmd or "$(" in cmd or "`" in cmd:
        state.is_valid = False
        state.error_message = "Unsafe format: command contains forbidden shell syntax."
        _log_validation(state.user_input, cmd, "REJECTED: Forbidden shell syntax")
        return state
    if _has_unquoted_shell_metacharacters(cmd):
        state.is_valid = False
        state.error_message = "Unsafe format: command contains unquoted shell metacharacters."
        _log_validation(state.user_input, cmd, "REJECTED: Unquoted shell metacharacters")
        return state

    try:
        tokens = _tokenize_command(cmd)
    except ValueError as exc:
        state.is_valid = False
        state.error_message = f"Unable to parse command safely: {exc}"
        _log_validation(state.user_input, cmd, f"REJECTED: Parse error - {exc}")
        return state

    if not tokens:
        state.is_valid = False
        state.error_message = "Empty tokenized command."
        _log_validation(state.user_input, cmd, "REJECTED: Empty after tokenization")
        return state

    executable = _get_executable_name(tokens[0])
    if executable not in {"ffmpeg", "ffmpeg.exe"}:
        state.is_valid = False
        state.error_message = "Invalid format: command must start with ffmpeg."
        _log_validation(state.user_input, cmd, f"REJECTED: Doesn't start with ffmpeg (got: {executable})")
        return state
    if any(_get_executable_name(token) in {"ffmpeg", "ffmpeg.exe"} for token in tokens[1:]):
        state.is_valid = False
        state.error_message = "Unsafe command: multiple ffmpeg invocations are not allowed."
        _log_validation(state.user_input, cmd, "REJECTED: Multiple ffmpeg invocations")
        return state

    lowered_tokens = [token.strip("'\"").lower() for token in tokens[1:]]

    for token in lowered_tokens:
        if token in SHELL_OPERATORS:
            state.is_valid = False
            state.error_message = "Unsafe command: shell chaining is not allowed."
            _log_validation(state.user_input, cmd, f"REJECTED: Shell operator '{token}'")
            return state
        if token in FORBIDDEN_TOKENS:
            state.is_valid = False
            state.error_message = f"Unsafe command: forbidden token '{token}' detected."
            _log_validation(state.user_input, cmd, f"REJECTED: Forbidden token '{token}'")
            return state
        if token.startswith(REDIRECTION_PREFIXES) or "&&" in token or "||" in token:
            state.is_valid = False
            state.error_message = "Unsafe command: shell redirection/chaining detected."
            _log_validation(state.user_input, cmd, f"REJECTED: Redirection/chaining in '{token}'")
            return state

    if "-i" not in lowered_tokens:
        state.is_valid = False
        state.error_message = "Invalid FFmpeg command: missing '-i' input."
        _log_validation(state.user_input, cmd, "REJECTED: Missing -i flag")
        return state

    encoder_family = (state.effective_encoder_family or "").lower()
    output_ext = _infer_output_extension(tokens)
    input_count = lowered_tokens.count("-i")
    has_explicit_video_codec = "-c:v" in lowered_tokens or "-vcodec" in lowered_tokens
    is_non_video_output = output_ext in NON_VIDEO_OUTPUT_EXTENSIONS
    nvidia_video_policy_applies = (
        encoder_family == "nvidia"
        and not is_non_video_output
        and (has_explicit_video_codec or output_ext in VIDEO_OUTPUT_EXTENSIONS or not output_ext)
    )
    if output_ext == ".gif":
        if input_count > 1:
            state.is_valid = False
            state.error_message = (
                "GIF policy violation: use a single input with a single-pass filter graph "
                "(no temp palette files or extra -i inputs)."
            )
            _log_validation(state.user_input, cmd, "REJECTED: GIF multiple-input temp-file pattern")
            return state
        has_palettegen = any("palettegen" in token for token in lowered_tokens)
        has_paletteuse = any("paletteuse" in token for token in lowered_tokens)
        if has_palettegen and not has_paletteuse:
            state.is_valid = False
            state.error_message = (
                "GIF policy violation: palettegen must be paired with paletteuse "
                "in the same command."
            )
            _log_validation(state.user_input, cmd, "REJECTED: GIF palettegen without paletteuse")
            return state
        if any(_is_palette_file(token) for token in tokens[1:]):
            state.is_valid = False
            state.error_message = (
                "GIF policy violation: temporary palette files are not allowed. "
                "Use an in-memory split/palettegen/paletteuse filter graph."
            )
            _log_validation(state.user_input, cmd, "REJECTED: GIF temporary palette file token")
            return state

    if encoder_family == "cpu" and any("nvenc" in token for token in lowered_tokens):
        state.is_valid = False
        state.error_message = "Encoder policy violation: NVENC is not allowed in CPU mode."
        _log_validation(state.user_input, cmd, "REJECTED: NVENC in CPU mode")
        return state
    if nvidia_video_policy_applies and any(token in {"libx264", "libx265"} for token in lowered_tokens):
        state.is_valid = False
        state.error_message = "Encoder policy violation: software x264/x265 is not allowed in NVIDIA mode."
        _log_validation(state.user_input, cmd, "REJECTED: Software encoder in NVIDIA mode")
        return state
    if nvidia_video_policy_applies and not any("nvenc" in token for token in lowered_tokens):
        state.is_valid = False
        state.error_message = "Encoder policy violation: NVIDIA mode requires an NVENC video encoder."
        _log_validation(state.user_input, cmd, "REJECTED: No NVENC encoder in NVIDIA mode")
        return state
    if nvidia_video_policy_applies and "-crf" in lowered_tokens:
        state.is_valid = False
        state.error_message = "Encoder policy violation: NVENC does not support '-crf'. Please use '-cq' instead of '-crf'."
        _log_validation(state.user_input, cmd, "REJECTED: CRF used with NVENC")
        return state
    if nvidia_video_policy_applies and "-preset" in lowered_tokens:
        preset_index = lowered_tokens.index("-preset")
        if preset_index + 1 < len(lowered_tokens):
            preset_value = lowered_tokens[preset_index + 1]
            if preset_value in {"ultrafast", "superfast", "veryfast", "faster"}:
                state.is_valid = False
                state.error_message = "Encoder policy violation: selected preset is not compatible with NVENC."
                _log_validation(state.user_input, cmd, f"REJECTED: Incompatible NVENC preset '{preset_value}'")
                return state
    if encoder_family == "cpu" and "-preset" in lowered_tokens:
        preset_index = lowered_tokens.index("-preset")
        if preset_index + 1 < len(lowered_tokens):
            preset_value = lowered_tokens[preset_index + 1]
            if preset_value in {"slow", "slower", "veryslow", "placebo"}:
                state.is_valid = False
                state.error_message = "Encoder policy violation: selected CPU preset is too heavy."
                _log_validation(state.user_input, cmd, f"REJECTED: CPU-heavy preset '{preset_value}'")
                return state

    state.is_valid = True
    state.error_message = None
    _log_validation(state.user_input, cmd, "APPROVED")
    return state

def _log_validation(user_input, command, result):
    """Logs validation results to generation.log for debugging."""
    log_file = "generation.log"
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    log_entry = (
        f"[{timestamp}] VALIDATION: {result}\n"
        f"USER INPUT: {user_input}\n"
        f"COMMAND: {command}\n"
        f"{'-'*80}\n"
    )
    
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(log_entry)
    except Exception:
        pass  # Don't fail if logging fails

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
        
        # Only accept explicit approval to avoid false positives like
        # "APPROVED but ..." slipping through.
        normalized_review = review.replace("*", "").replace("`", "").strip()

        if normalized_review.upper() in {"APPROVED", "APPROVED."}:
            state.is_valid = True
        else:
            state.is_valid = False
            state.generated_command = None  # Clear command on security failure
            state.error_message = f"Security Audit Failed: {review}"
    except Exception as exc:
        state.is_valid = False
        state.generated_command = None  # Clear command on audit failure
        state.error_message = f"Safety Audit unreachable: {exc}"

    return state
