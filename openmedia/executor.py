import os
import subprocess
from pathlib import Path
from openmedia.constants import (
    NON_VIDEO_OUTPUT_EXTENSIONS,
    VIDEO_OUTPUT_EXTENSIONS,
    tokenize_command,
)
from openmedia.utils import log_command

def _build_command(tokens):
    return subprocess.list2cmdline(tokens)

def _upsert_option(tokens, option, value):
    if option in tokens:
        idx = tokens.index(option)
        if idx + 1 < len(tokens):
            tokens[idx + 1] = str(value)
        else:
            tokens.append(str(value))
        return

    insert_at = 1 if tokens else 0
    tokens.insert(insert_at, option)
    tokens.insert(insert_at + 1, str(value))

def _contains_option(tokens, option):
    return option in tokens

def _remove_option(tokens, option):
    removed = False
    while option in tokens:
        idx = tokens.index(option)
        del tokens[idx]
        if idx < len(tokens) and not tokens[idx].startswith("-"):
            del tokens[idx]
        removed = True
    return removed

def _get_option_value(tokens, option):
    if option in tokens:
        idx = tokens.index(option)
        if idx + 1 < len(tokens):
            return tokens[idx + 1]
    return None

def _cap_cpu_threads(ultra_safe=False):
    cpu_count = os.cpu_count() or 2
    ratio = 0.4 if ultra_safe else 0.6
    return max(1, int(cpu_count * ratio))

def _strip_quotes(token):
    return token.strip("'\"")

def _same_path(a, b):
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))

def _default_output_path(target_file):
    base = Path(target_file) if target_file else Path("output.mp4")
    suffix = base.suffix if base.suffix else ".mp4"
    return str(base.with_name(f"{base.stem}_openmedia{suffix}"))

def _next_available_output(path_str):
    path = Path(path_str)
    if not path.exists():
        return str(path)

    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    idx = 1
    while True:
        candidate = parent / f"{stem}_{idx:03d}{suffix}"
        if not candidate.exists():
            return str(candidate)
        idx += 1

def _extract_primary_input(tokens, target_file):
    if target_file:
        return target_file
    for idx, token in enumerate(tokens):
        if token.lower() == "-i" and idx + 1 < len(tokens):
            return _strip_quotes(tokens[idx + 1])
    return "output.mp4"

def _pin_primary_input(tokens, target_file):
    """
    Normalizes the first -i input path to an absolute, deterministic path when
    target_file is known. This reduces quoting/path issues with LLM output.
    """
    if not target_file:
        return False

    resolved = str(Path(target_file).expanduser().resolve(strict=False))
    for idx, token in enumerate(tokens):
        if token.lower() == "-i" and idx + 1 < len(tokens):
            if _strip_quotes(tokens[idx + 1]) != resolved:
                tokens[idx + 1] = resolved
                return True
            return False
    return False

def _infer_output_extension(tokens, target_file=None):
    for token in reversed(tokens[1:]):
        cleaned = _strip_quotes(token)
        if not cleaned or cleaned.startswith("-"):
            continue
        ext = Path(cleaned).suffix.lower()
        if ext:
            return ext
    if target_file:
        return Path(target_file).suffix.lower()
    return ""

def _ensure_safe_output_target(tokens, target_file):
    """
    Ensures output naming policy:
    - auto suffix defaults when output is missing/placeholder
    - avoid overwriting existing files
    """
    if not tokens:
        return False

    changed = False
    primary_input = _extract_primary_input(tokens, target_file)
    sink_outputs = {"-", "nul", "/dev/null"}

    output_idx = len(tokens) - 1
    output_raw = _strip_quotes(tokens[output_idx]) if output_idx >= 0 else ""

    if output_raw.lower() in sink_outputs:
        return False

    if not output_raw or output_raw.startswith("-"):
        output_path = _default_output_path(primary_input)
        output_path = _next_available_output(output_path)
        if output_idx >= 0 and not output_raw.startswith("-"):
            tokens[output_idx] = output_path
        else:
            tokens.append(output_path)
        return True
    if output_raw.lower() == "output_file.mp4":
        output_path = _default_output_path(primary_input)
        output_path = _next_available_output(output_path)
        tokens[output_idx] = output_path
        return True

    output_path = output_raw
    if not Path(output_path).suffix:
        input_suffix = Path(primary_input).suffix or ".mp4"
        output_path = f"{output_path}{input_suffix}"

    if primary_input and _same_path(output_path, primary_input):
        output_path = _default_output_path(primary_input)

    output_path = _next_available_output(output_path)
    if output_path != output_raw:
        tokens[output_idx] = output_path
        changed = True

    return changed

def prepare_command_for_safe_execution(
    command, encoder_family, target_file=None, ultra_safe=False
):
    """
    Applies conservative defaults to reduce CPU/thermal stress.
    Returns (prepared_command, changed).
    """
    try:
        tokens = tokenize_command(command)
    except ValueError:
        return command, False

    if not tokens:
        return command, False

    changed = False
    if _pin_primary_input(tokens, target_file):
        changed = True

    safe_threads = str(_cap_cpu_threads(ultra_safe))
    output_ext = _infer_output_extension(tokens, target_file)
    is_non_video_target = output_ext in NON_VIDEO_OUTPUT_EXTENSIONS
    is_video_target = (output_ext in VIDEO_OUTPUT_EXTENSIONS) or (not output_ext and not is_non_video_target)

    if not _contains_option(tokens, "-threads"):
        _upsert_option(tokens, "-threads", safe_threads)
        changed = True

    family = (encoder_family or "").lower()
    if family == "cpu" and is_video_target:
        _upsert_option(tokens, "-c:v", "libx264")
        _upsert_option(tokens, "-preset", "superfast" if ultra_safe else "veryfast")
        changed = True

        has_crf = _contains_option(tokens, "-crf")
        has_bitrate = _contains_option(tokens, "-b:v")
        if not has_crf and not has_bitrate:
            _upsert_option(tokens, "-crf", "25" if ultra_safe else "23")
            changed = True
    elif family == "nvidia" and is_video_target:
        codec_value = (_get_option_value(tokens, "-c:v") or "").lower()
        if not codec_value.endswith("nvenc"):
            _upsert_option(tokens, "-c:v", "h264_nvenc")
            changed = True

        preset_value = (_get_option_value(tokens, "-preset") or "").lower()
        if preset_value in {"ultrafast", "superfast", "veryfast", "faster"}:
            _upsert_option(tokens, "-preset", "fast")
            changed = True
        elif not preset_value:
            _upsert_option(tokens, "-preset", "fast")
            changed = True

        crf_value = _get_option_value(tokens, "-crf")
        if crf_value is not None:
            _remove_option(tokens, "-crf")
            if not _contains_option(tokens, "-cq") and not _contains_option(tokens, "-b:v"):
                _upsert_option(tokens, "-cq", crf_value)
            changed = True
        elif not _contains_option(tokens, "-cq") and not _contains_option(tokens, "-b:v"):
            _upsert_option(tokens, "-cq", "30" if ultra_safe else "28")
            changed = True

    if _ensure_safe_output_target(tokens, target_file):
        changed = True

    prepared = _build_command(tokens)
    return prepared, changed

def _process_priority_kwargs():
    """
    Uses lower process priority to keep system responsive and reduce sustained stress.
    """
    kwargs = {}
    if os.name == "nt":
        below_normal = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        if below_normal:
            kwargs["creationflags"] = below_normal
    elif os.name == "posix":
        kwargs["preexec_fn"] = lambda: os.nice(5)
    return kwargs

def run_ffmpeg(
    command, user_prompt, encoder_family="cpu", target_file=None, ultra_safe=False, timeout=1800
):
    """
    Executes FFmpeg command with safety measures.
    
    Args:
        command: FFmpeg command to execute
        user_prompt: Original user request for logging
        encoder_family: Encoder family (cpu/nvidia)
        target_file: Input file path
        ultra_safe: Whether to use ultra-safe mode
        timeout: Maximum execution time in seconds (default: 1800 = 30 minutes)
    
    Returns:
        Tuple of (success: bool, output: str)
    """
    prepared_command, _ = prepare_command_for_safe_execution(
        command, encoder_family, target_file, ultra_safe
    )

    try:
        prepared_tokens = tokenize_command(prepared_command)
        if not prepared_tokens:
            log_command(user_prompt, prepared_command, "FAILED (Empty prepared command)")
            return False, "Prepared command is empty."

        if (encoder_family or "").lower() == "nvidia":
            # Allow a brief GPU memory handoff window after Ollama unload.
            import time
            time.sleep(0.5)

        result = subprocess.run(
            prepared_tokens,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            **_process_priority_kwargs()
        )
        
        # If it reaches here, it was successful
        log_command(user_prompt, prepared_command, "SUCCESS")
        output = (result.stderr or result.stdout or "").strip()
        return True, output
    except ValueError as exc:
        log_command(user_prompt, prepared_command, "FAILED (Command parse error)")
        return False, f"Prepared command parsing failed: {exc}"
    except subprocess.TimeoutExpired:
        log_command(user_prompt, prepared_command, f"TIMEOUT (>{timeout}s)")
        return False, f"FFmpeg execution timed out after {timeout} seconds. Try a smaller file or simpler operation."
    except subprocess.CalledProcessError as e:
        # If FFmpeg fails, we log the failure too
        log_command(user_prompt, prepared_command, f"FAILED (Exit Code: {e.returncode})")
        error_text = (e.stderr or e.stdout or str(e)).strip()
        return False, error_text

