import datetime
import json
import os
import shutil
import subprocess
from pathlib import Path

MEDIA_EXTENSIONS = (
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".mp3",
    ".wav",
    ".flac",
    ".aac",
    ".ogg",
    ".m4a",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
)
APP_DIR = Path.home() / ".openmedia"
LOG_DIR = APP_DIR / "logs"
CONFIG_FILE = APP_DIR / "config.json"
LEGACY_CONFIG_FILE = Path.home() / ".openmedia_config.json"

def get_media_type(file_path):
    """Categorizes a file based on its extension."""
    ext = Path(file_path).suffix.lower()
    if ext in ('.mp4', '.mkv', '.mov', '.avi', '.webm'):
        return "video"
    if ext in ('.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a'):
        return "audio"
    if ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'):
        return "image"
    return "unknown"

def format_size(size_bytes):
    """Formats bytes into a human-readable string (MB, GB)."""
    try:
        size = float(size_bytes)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
    except (ValueError, TypeError):
        return "Unknown"
    return "Unknown"

def format_bitrate(bitrate_bps):
    """Formats bitrate (bps) into a human-readable string (kbps, Mbps)."""
    try:
        bitrate = float(bitrate_bps)
        for unit in ["bps", "kbps", "Mbps", "Gbps"]:
            if bitrate < 1000.0:
                return f"{bitrate:.2f} {unit}"
            bitrate /= 1000.0
    except (ValueError, TypeError):
        return "Unknown"
    return "Unknown"

def get_local_media_files():
    """Returns a list of media files in the current working directory."""
    return [f for f in os.listdir('.') if f.lower().endswith(MEDIA_EXTENSIONS)]

def find_best_match(user_query, files):
    """Finds if a specific filename is mentioned in the user's natural language query."""
    for f in files:
        name_only = os.path.splitext(f)[0].lower()
        if name_only in user_query.lower():
            return f
    return None

def load_config():
    """Loads persistent user config from disk."""
    config_path = CONFIG_FILE if CONFIG_FILE.exists() else LEGACY_CONFIG_FILE
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

def ensure_log_dir():
    """Ensures the application log directory exists."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR

def get_log_path(file_name):
    """Returns an absolute path inside the OpenMedia log directory."""
    return ensure_log_dir() / file_name

def save_config(config):
    """Saves persistent user config to disk."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")

def clear_config():
    """Clears persistent user config from disk."""
    if CONFIG_FILE.exists():
        CONFIG_FILE.unlink()
    if LEGACY_CONFIG_FILE.exists():
        LEGACY_CONFIG_FILE.unlink()

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
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
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
    
    if metadata is None:
        return "Unavailable (No metadata)"

    fmt = metadata.get("format", {})
    streams = metadata.get("streams", [])

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

    duration = fmt.get("duration", "unknown")
    container = fmt.get("format_name", "unknown")
    bit_rate = fmt.get("bit_rate", "unknown")
    size = fmt.get("size", "unknown")

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
    """Appends command execution history in JSONL format."""
    log_file = get_log_path("history.jsonl")
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = {
        "timestamp": timestamp,
        "status": status,
        "prompt": user_prompt,
        "command": generated_command,
    }

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=True) + "\n")

def check_ollama_model(model_name="qwen2.5-coder:7b-instruct-q4_K_M"):
    """Checks if a specific Ollama model is available."""
    try:
        import requests
        response = requests.get("http://localhost:11434/api/tags", timeout=5)
        response.raise_for_status()
        models = response.json().get("models", [])
        return any(model_name in m.get("name", "") for m in models)
    except Exception:
        return False

def check_environment():
    """
    Returns a list of required tools missing from PATH.
    """
    required = ["ffmpeg", "ffprobe", "ollama"]
    return [dep for dep in required if shutil.which(dep) is None]
