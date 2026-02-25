import datetime
import json
import os
import subprocess
from pathlib import Path

VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.mov', '.avi', '.mp3', '.wav', '.flac')
CONFIG_FILE = Path.home() / ".openmedia_config.json"

def get_local_media_files():
    """Returns a list of media files in the current working directory."""
    return [f for f in os.listdir('.') if f.lower().endswith(VIDEO_EXTENSIONS)]

def find_best_match(user_query, files):
    """Finds if a specific filename is mentioned in the user's natural language query."""
    for f in files:
        name_only = os.path.splitext(f)[0].lower()
        if name_only in user_query.lower():
            return f
    return None

def load_config():
    """Loads persistent user config from disk."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_config(config):
    """Saves persistent user config to disk."""
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")

def clear_config():
    """Clears persistent user config from disk."""
    if CONFIG_FILE.exists():
        CONFIG_FILE.unlink()

def get_encoding_preference():
    """
    Returns saved encoding preference:
    - 'auto'
    - 'nvidia'
    - 'cpu'
    """
    return load_config().get("encoding_preference")

def set_encoding_preference(preference):
    """Persists encoding preference for future runs."""
    config = load_config()
    config["encoding_preference"] = preference
    save_config(config)

def get_llm_runtime_mode():
    """
    Returns saved Ollama runtime mode:
    - 'auto'
    - 'gpu'
    - 'cpu'
    """
    return load_config().get("llm_runtime_mode")

def set_llm_runtime_mode(mode):
    """Persists Ollama runtime mode for future runs."""
    config = load_config()
    config["llm_runtime_mode"] = mode
    save_config(config)

def is_nvenc_available():
    """Detects NVENC support by checking ffmpeg encoders output."""
    command = ["ffmpeg", "-hide_banner", "-encoders"]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        output = (result.stdout or "") + (result.stderr or "")
        normalized = output.lower()
        return ("h264_nvenc" in normalized) or ("hevc_nvenc" in normalized)
    except Exception:
        return False

def probe_media_file(file_path):
    """Returns ffprobe JSON data for a media file or (None, error_message) on failure."""
    command = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=format_name,duration,size,bit_rate:stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
        "-of", "json",
        file_path,
    ]

    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        return json.loads(result.stdout), None
    except FileNotFoundError:
        return None, "ffprobe not found in PATH"
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or str(exc)).strip()
        return None, f"ffprobe failed: {details}"
    except json.JSONDecodeError:
        return None, "ffprobe returned invalid JSON"

def build_media_context(file_path):
    """Builds a concise media summary for prompt context."""
    metadata, error = probe_media_file(file_path)
    if error:
        return f"Unavailable ({error})"

    format_info = metadata.get("format", {})
    streams = metadata.get("streams", [])

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

    duration = format_info.get("duration", "unknown")
    container = format_info.get("format_name", "unknown")
    bit_rate = format_info.get("bit_rate", "unknown")
    size = format_info.get("size", "unknown")

    video_codec = video_stream.get("codec_name", "none")
    width = video_stream.get("width", "unknown")
    height = video_stream.get("height", "unknown")
    frame_rate = video_stream.get("r_frame_rate", "unknown")

    audio_codec = audio_stream.get("codec_name", "none")
    sample_rate = audio_stream.get("sample_rate", "unknown")
    channels = audio_stream.get("channels", "unknown")

    return (
        f"container={container}, duration={duration}, size={size}, bit_rate={bit_rate}; "
        f"video(codec={video_codec}, resolution={width}x{height}, fps={frame_rate}); "
        f"audio(codec={audio_codec}, sample_rate={sample_rate}, channels={channels})"
    )

def log_command(user_prompt, generated_command, status="SUCCESS"):
    """Saves the command history to a local text file."""
    log_file = "history.log"
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    log_entry = (
        f"[{timestamp}] STATUS: {status}\n"
        f"PROMPT: {user_prompt}\n"
        f"COMMAND: {generated_command}\n"
        f"{'-'*50}\n"
    )
    
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(log_entry)
