import argparse
import re
from pathlib import Path

import questionary
import requests
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from questionary import Style

from openmedia.graph import media_agent
from openmedia.nodes import safety_reviewer_node, validator_node
from openmedia.state import AgentState
from . import executor, utils

console = Console()

# Custom style for questionary to match the new aesthetic
OM_STYLE = Style([
    ('qmark', 'fg:#00ffff bold'),
    ('question', 'bold'),
    ('answer', 'fg:#00ff00 bold'),
    ('pointer', 'fg:#00ffff bold'),
    ('highlighted', 'fg:#00ffff bold'),
    ('selected', 'fg:#00ff00'),
    ('separator', 'fg:#666666'),
    ('instruction', 'fg:#888888 italic'),
    ('text', 'fg:#ffffff'),
])


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="OpenMedia CLI")
    
    # Create subparsers for commands like 'info'
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # 'info' command
    info_parser = subparsers.add_parser("info", help="Display technical information about a media file.")
    info_parser.add_argument("path", help="Path to the media file.")

    # Main interactive mode flags
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and validate command only; do not execute FFmpeg.",
    )
    parser.add_argument(
        "--settings",
        action="store_true",
        help="Open settings editor and exit.",
    )
    parser.add_argument(
        "--ultra-safe",
        action="store_true",
        help="Use lower-resource defaults for smoother thermals and responsiveness.",
    )
    return parser.parse_args(argv)


def display_media_info(file_path):
    """Displays a professional technical table of the media file's streams."""
    metadata, error = utils.probe_media_file(file_path)
    if error:
        console.print(f"[bold red]Error probing file:[/] {error}")
        return

    fmt = metadata.get("format", {})
    streams = metadata.get("streams", [])
    raw_duration = fmt.get("duration", 0)
    try:
        duration_text = f"{float(raw_duration):.2f}s"
    except (ValueError, TypeError):
        duration_text = "unknown"

    # Global File Info Panel
    file_info = (
        f"[bold white]File:[/] {Path(file_path).name}\n"
        f"[bold white]Size:[/] {utils.format_size(fmt.get('size'))} | "
        f"[bold white]Duration:[/] {duration_text} | "
        f"[bold white]Bitrate:[/] {utils.format_bitrate(fmt.get('bit_rate'))}"
    )
    console.print(Panel(file_info, title="[bold cyan]Media Technical Summary[/]", expand=False, border_style="cyan"))

    # Streams Table
    table = Table(title="[bold white]Stream Mapping[/]", header_style="bold magenta", expand=False)
    table.add_column("Idx", justify="right", style="dim")
    table.add_column("Type", justify="center")
    table.add_column("Codec", style="bold")
    table.add_column("Details", style="white")

    for s in streams:
        idx = s.get("index")
        ctype = s.get("codec_type", "unknown").upper()
        codec = s.get("codec_name", "unknown")
        
        details = ""
        if ctype == "VIDEO":
            color = "green"
            res = f"{s.get('width')}x{s.get('height')}"
            fps = s.get("r_frame_rate", "0/0")
            try:
                num, den = map(int, fps.split('/'))
                fps_val = f"{num/den:.2f} fps" if den != 0 else "unknown"
            except (ValueError, ZeroDivisionError, AttributeError):
                fps_val = str(fps)
            pix_fmt = s.get("pix_fmt", "unknown")
            details = f"{res} | {fps_val} | {pix_fmt}"
        elif ctype == "AUDIO":
            color = "cyan"
            channels = s.get("channels", "unknown")
            sr = s.get("sample_rate", "unknown")
            if isinstance(sr, (int, float)):
                details = f"{channels} ch | {int(sr)/1000:.1f} kHz"
            elif isinstance(sr, str) and sr.isdigit():
                details = f"{channels} ch | {int(sr)/1000:.1f} kHz"
            else:
                details = f"{channels} ch | {sr} kHz"
        else:
            color = "magenta"
            details = s.get("tags", {}).get("language", "unknown")

        table.add_row(
            str(idx),
            f"[bold {color}]{ctype}[/]",
            codec,
            details
        )

    console.print(table)


def check_ollama():
    """Ping Ollama to ensure the server is running."""
    try:
        requests.get("http://localhost:11434/", timeout=2)
        return True
    except requests.exceptions.RequestException:
        return False


def check_ollama_model_available():
    """Check if the required Ollama model is available."""
    return utils.check_ollama_model("qwen2.5-coder:7b-instruct-q4_K_M")


def open_settings_menu():
    while True:
        current_encoding = utils.get_encoding_preference() or "not set"
        current_llm = utils.get_llm_runtime_mode() or "not set"

        choice = questionary.select(
            "Settings",
            choices=[
                f"Encoding mode [{current_encoding}]",
                f"Ollama mode [{current_llm}]",
                "Reset all saved settings",
                "Back",
            ],
            style=OM_STYLE
        ).ask()

        if not choice or choice == "Back":
            return

        if choice.startswith("Encoding mode"):
            encoding = questionary.select(
                "Default encoding mode:",
                choices=["auto", "nvidia", "cpu"],
                style=OM_STYLE
            ).ask()
            if encoding:
                utils.set_encoding_preference(encoding)
                console.print(f"[green]Saved encoding mode: {encoding}[/]")
            continue

        if choice.startswith("Ollama mode"):
            llm_mode = questionary.select(
                "Default Ollama runtime mode:",
                choices=["auto", "gpu", "cpu"],
                style=OM_STYLE
            ).ask()
            if llm_mode:
                utils.set_llm_runtime_mode(llm_mode)
                console.print(f"[green]Saved Ollama mode: {llm_mode}[/]")
            continue

        if choice == "Reset all saved settings":
            confirmed = questionary.confirm("Reset all saved settings?", style=OM_STYLE).ask()
            if confirmed:
                utils.clear_config()
                console.print("[green]Saved settings reset.[/]")
            continue


def _resolve_encoding_mode():
    encoding_preference = utils.get_encoding_preference()
    if encoding_preference not in {"auto", "nvidia", "cpu"}:
        encoding_preference = None

    if not encoding_preference:
        encoding_preference = questionary.select(
            "Choose default encoding mode (saved for future runs):",
            choices=["auto", "nvidia", "cpu"],
            style=OM_STYLE
        ).ask()
        if not encoding_preference:
            return None, None, None
        utils.set_encoding_preference(encoding_preference)

    nvenc_available = utils.is_nvenc_available()
    effective_encoder_family = encoding_preference
    if encoding_preference == "auto":
        effective_encoder_family = "nvidia" if nvenc_available else "cpu"
    elif encoding_preference == "nvidia" and not nvenc_available:
        console.print(
            "[yellow]NVIDIA encoder requested but NVENC is unavailable. "
            "Falling back to CPU (libx264).[/]"
        )
        effective_encoder_family = "cpu"

    return encoding_preference, effective_encoder_family, nvenc_available


def _resolve_llm_mode(effective_encoder_family, nvenc_available):
    llm_runtime_mode = utils.get_llm_runtime_mode()
    if llm_runtime_mode not in {"auto", "gpu", "cpu"}:
        llm_runtime_mode = None

    if not llm_runtime_mode:
        llm_runtime_mode = questionary.select(
            "Choose Ollama runtime mode (saved for future runs):",
            choices=["auto", "gpu", "cpu"],
            style=OM_STYLE
        ).ask()
        if not llm_runtime_mode:
            return None, None
        utils.set_llm_runtime_mode(llm_runtime_mode)

    llm_effective_mode = llm_runtime_mode
    if llm_runtime_mode == "auto":
        llm_effective_mode = "cpu" if effective_encoder_family == "nvidia" else "gpu"
    elif llm_runtime_mode == "gpu" and not nvenc_available:
        console.print(
            "[yellow]GPU mode requested for Ollama but GPU encode support is unavailable. "
            "Falling back to CPU inference.[/]"
        )
        llm_effective_mode = "cpu"

    return llm_runtime_mode, llm_effective_mode


def _suggest_output_name(target_file, suffix, new_ext=None):
    target_path = Path(target_file)
    ext = new_ext if new_ext else (target_path.suffix if target_path.suffix else ".mp4")
    return f"{target_path.stem}_{suffix}{ext}"


def _ask_output_name(prompt, default_name):
    output_name = questionary.text(prompt, default=default_name, style=OM_STYLE).ask()
    if not output_name:
        return default_name
    return output_name.strip().strip('"')


def _select_target_file():
    while True:
        local_files = utils.get_local_media_files()
        choices = []
        if local_files:
            choices.extend(local_files)
        choices.extend(["Type a path manually", "Cancel"])

        choice = questionary.select(
            "Select the media file to process:",
            choices=choices,
            style=OM_STYLE
        ).ask()

        if not choice or choice == "Cancel":
            return None

        if choice == "Type a path manually":
            manual_path = questionary.text("Enter file path:", style=OM_STYLE).ask()
            if not manual_path:
                return None
            manual_path = manual_path.strip().strip('"')
            if not Path(manual_path).exists():
                console.print(f"[yellow]File not found: {manual_path}[/]")
                retry = questionary.confirm("Try another path?", style=OM_STYLE).ask()
                if retry:
                    continue
                return None
            return manual_path

        return choice


def _collect_user_request(target_file):
    target_name = Path(target_file).name
    mtype = utils.get_media_type(target_file)

    while True:
        choices = []
        if mtype == "video":
            choices = [
                "Compress for sharing (Recommended)",
                "Change file format",
                "Create Animated GIF",
                "Extract audio only",
                "Remove audio from video",
            ]
        elif mtype == "audio":
            choices = [
                "Compress for sharing (Recommended)",
                "Change file format",
            ]
        elif mtype == "image":
            choices = [
                "Compress / Optimize",
                "Convert to WebP (Best for Web)",
                "Change image format",
                "Resize image",
            ]
        
        choices.extend(["Custom request", "Open settings", "Cancel"])

        action = questionary.select(
            "What would you like to do?",
            choices=choices,
            style=OM_STYLE
        ).ask()

        if not action or action == "Cancel":
            return None

        if action == "Open settings":
            open_settings_menu()
            continue

        if action == "Custom request":
            query = questionary.text(
                "Describe your request:",
                default=f"Optimize {target_name} for sharing while preserving quality.",
                style=OM_STYLE
            ).ask()
            if not query:
                return None
            if query.strip().lower() == "/settings":
                open_settings_menu()
                continue
            return query

        if action in ["Compress for sharing (Recommended)", "Compress / Optimize"]:
            if mtype == "image":
                quality_choice = questionary.select(
                    "Optimization target:",
                    choices=["Lossless (Perfect Quality)", "Balanced", "Small File Size"],
                    style=OM_STYLE
                ).ask()
                return f"Optimize {target_name} using {quality_choice} compression. Keep it as {target_name}."
            
            quality_choice = questionary.select(
                "Compression target:",
                choices=[
                    "Balanced size and quality (Recommended)",
                    "Smaller file size",
                    "Higher quality",
                ],
                style=OM_STYLE
            ).ask()
            resolution_choice = questionary.select(
                "Output resolution:",
                choices=["Keep original (Recommended)", "1080p", "720p"],
                style=OM_STYLE
            ).ask()
            fps_choice = questionary.select(
                "Frame rate:",
                choices=["Keep original (Recommended)", "60 fps", "30 fps"],
                style=OM_STYLE
            ).ask()
            output_name = _ask_output_name(
                "Output filename:",
                _suggest_output_name(target_file, "share", ".mp4"),
            )

            quality_instruction = {
                "Balanced size and quality (Recommended)": "balanced compression",
                "Smaller file size": "stronger compression for a smaller file",
                "Higher quality": "higher-quality compression with a larger file",
            }[quality_choice]
            resolution_instruction = {
                "Keep original (Recommended)": "Keep original resolution",
                "1080p": "Scale to 1080p",
                "720p": "Scale to 720p",
            }[resolution_choice]
            fps_instruction = {
                "Keep original (Recommended)": "Keep original frame rate",
                "60 fps": "Use 60 fps",
                "30 fps": "Use 30 fps",
            }[fps_choice]

            return (
                f"Compress {target_name} for sharing using {quality_instruction}. "
                f"{resolution_instruction}. {fps_instruction}. "
                f"Preserve clear audio and save as {output_name}."
            )

        if action == "Create Animated GIF":
            fps = questionary.select(
                "GIF Frame Rate (Higher is smoother, larger file):",
                choices=["15 fps (Standard)", "30 fps (High Quality)", "10 fps (Smallest)"],
                style=OM_STYLE
            ).ask()
            width = questionary.select(
                "GIF Width (Resolution):",
                choices=["480px (Recommended)", "720px (HD GIF)", "320px (Small/Emoji size)"],
                style=OM_STYLE
            ).ask()
            output_name = _ask_output_name(
                "Output filename:",
                _suggest_output_name(target_file, "anim", ".gif"),
            )
            return (
                f"Create a high-quality animated GIF from {target_name}. "
                f"Set frame rate to {fps} and width to {width}. "
                f"Use a professional palettegen filter for best colors in a single ffmpeg command "
                f"(no command chaining, no temporary files). Save as {output_name}."
            )

        if action == "Convert to WebP (Best for Web)":
            quality = questionary.select(
                "WebP Type:",
                choices=["Lossless (Perfect Quality)", "Lossy (Smallest File)"],
                style=OM_STYLE
            ).ask()
            output_name = _ask_output_name(
                "Output filename:",
                _suggest_output_name(target_file, "optimized", ".webp"),
            )
            return f"Convert {target_name} to {quality} WebP format for web optimization. Save as {output_name}."

        if action in ["Change file format", "Change image format"]:
            if mtype == "video":
                fmt_choices = ["mp4", "mkv", "mov", "webm"]
            elif mtype == "audio":
                fmt_choices = ["mp3", "wav", "aac", "flac"]
            elif mtype == "image":
                fmt_choices = ["jpg", "png", "webp", "bmp"]
            
            target_format = questionary.select(
                "Choose output format:",
                choices=fmt_choices,
                style=OM_STYLE
            ).ask()
            output_name = _ask_output_name(
                "Output filename:",
                _suggest_output_name(target_file, "converted", f".{target_format}"),
            )
            return (
                f"Convert {target_name} to {target_format.upper()} with good quality. "
                f"Save as {output_name}."
            )

        if action == "Resize image":
            size = questionary.select(
                "Scale image to:",
                choices=["50% smaller", "200% larger (Upscale)", "Custom resolution"],
                style=OM_STYLE
            ).ask()
            if size == "Custom resolution":
                res = questionary.text("Enter width:height (e.g. 1920:1080):", style=OM_STYLE).ask()
                size = f"at {res} resolution"
            
            output_name = _ask_output_name(
                "Output filename:",
                _suggest_output_name(target_file, "resized"),
            )
            return f"Resize {target_name} to {size} and save as {output_name}."

        if action == "Extract audio only":
            audio_format = questionary.select(
                "Choose audio format:",
                choices=["mp3", "aac", "wav"],
                style=OM_STYLE
            ).ask()
            output_name = _ask_output_name(
                "Output filename:",
                _suggest_output_name(target_file, "audio", f".{audio_format}"),
            )
            return (
                f"Extract audio only from {target_name} and save as {output_name}. "
                f"Keep clear listening quality."
            )

        if action == "Remove audio from video":
            output_name = _ask_output_name(
                "Output filename:",
                _suggest_output_name(target_file, "muted", Path(target_file).suffix or ".mp4"),
            )
            return f"Remove audio from {target_name} while keeping video quality. Save as {output_name}."


def _build_gif_fallback_command(query, target_file):
    """Builds a deterministic single-pass GIF command when LLM retries are exhausted."""
    fps = 15
    width = 480
    output_name = _suggest_output_name(target_file, "anim", ".gif")

    fps_match = re.search(r"(\d{1,3})\s*fps", query, flags=re.IGNORECASE)
    if fps_match:
        fps = max(1, min(120, int(fps_match.group(1))))

    width_match = re.search(r"(\d{2,5})\s*px", query, flags=re.IGNORECASE)
    if width_match:
        width = max(64, min(4096, int(width_match.group(1))))

    output_match = re.search(r"save as\s+([^\n\r]+?\.gif)\b", query, flags=re.IGNORECASE)
    if output_match:
        candidate = output_match.group(1).strip().strip('"').strip("'")
        if candidate:
            output_name = candidate

    return (
        f'ffmpeg -i "{target_file}" '
        f'-filter_complex '
        f'"[0:v]fps={fps},scale={width}:-1:flags=lanczos,split[s0][s1];'
        f'[s0]palettegen[p];[s1][p]paletteuse" '
        f'"{output_name}"'
    )

def _audit_command_for_execution(
    command, user_input, target_file, media_context, effective_encoder_family
):
    """
    Ensures deterministic fallback commands pass the same validator + reviewer pipeline
    before execution.
    """
    audit_state = AgentState(
        user_input=user_input,
        target_file=target_file,
        media_context=media_context,
        effective_encoder_family=effective_encoder_family,
        generated_command=command,
        is_valid=True,
        iteration_count=0,
        history=[],
    )
    audit_state = validator_node(audit_state)
    if audit_state.is_valid:
        audit_state = safety_reviewer_node(audit_state)
    return audit_state

def _build_webp_fallback_command(query, target_file):
    """Builds a deterministic WebP conversion command when LLM retries are exhausted."""
    output_name = _suggest_output_name(target_file, "optimized", ".webp")
    output_match = re.search(r"save as\s+([^\n\r]+?\.webp)\b", query, flags=re.IGNORECASE)
    if output_match:
        candidate = output_match.group(1).strip().strip('"').strip("'")
        if candidate:
            output_name = candidate

    if "lossless" in query.lower():
        return f'ffmpeg -i "{target_file}" -lossless 1 "{output_name}"'
    return f'ffmpeg -i "{target_file}" -quality 75 "{output_name}"'

def _extract_output_name_from_query(query, extension):
    pattern = rf"save as\s+([^\n\r]+?\{extension})\b"
    match = re.search(pattern, query, flags=re.IGNORECASE)
    if not match:
        return None
    candidate = match.group(1).strip().strip('"').strip("'")
    return candidate or None

def _build_remove_audio_fallback_command(query, target_file):
    output_name = _extract_output_name_from_query(query, ".mp4")
    if not output_name:
        output_name = _suggest_output_name(target_file, "muted", Path(target_file).suffix or ".mp4")
    return f'ffmpeg -i "{target_file}" -an -c:v copy "{output_name}"'

def _build_extract_audio_fallback_command(query, target_file):
    output_name = _extract_output_name_from_query(query, ".mp3")
    if not output_name:
        output_name = _extract_output_name_from_query(query, ".aac")
    if not output_name:
        output_name = _extract_output_name_from_query(query, ".wav")
    if not output_name:
        output_name = _suggest_output_name(target_file, "audio", ".mp3")

    ext = Path(output_name).suffix.lower()
    if ext == ".wav":
        return f'ffmpeg -i "{target_file}" -vn -c:a pcm_s16le "{output_name}"'
    if ext == ".aac":
        return f'ffmpeg -i "{target_file}" -vn -c:a aac -b:a 192k "{output_name}"'
    return f'ffmpeg -i "{target_file}" -vn -c:a libmp3lame -q:a 2 "{output_name}"'

def _build_resize_image_fallback_command(query, target_file):
    output_name = _extract_output_name_from_query(query, Path(target_file).suffix or ".png")
    if not output_name:
        output_name = _suggest_output_name(target_file, "resized", Path(target_file).suffix or ".png")

    ql = query.lower()
    if "50% smaller" in ql:
        scale = "iw/2:ih/2"
    elif "200% larger" in ql:
        scale = "iw*2:ih*2"
    else:
        custom = re.search(r"at\s+(\d{2,5}:\d{2,5})\s+resolution", ql)
        scale = custom.group(1) if custom else "iw:ih"

    return f'ffmpeg -i "{target_file}" -vf "scale={scale}" "{output_name}"'

def _build_change_format_fallback_command(query, target_file, encoder_family):
    output = None
    for ext in (".mp4", ".mkv", ".mov", ".webm", ".mp3", ".wav", ".aac", ".flac", ".jpg", ".png", ".webp", ".bmp"):
        output = _extract_output_name_from_query(query, ext)
        if output:
            break
    if not output:
        return None

    output_ext = Path(output).suffix.lower()
    if output_ext in {".mp3", ".wav", ".aac", ".flac"}:
        if output_ext == ".wav":
            return f'ffmpeg -i "{target_file}" -vn -c:a pcm_s16le "{output}"'
        if output_ext == ".aac":
            return f'ffmpeg -i "{target_file}" -vn -c:a aac -b:a 192k "{output}"'
        if output_ext == ".flac":
            return f'ffmpeg -i "{target_file}" -vn -c:a flac "{output}"'
        return f'ffmpeg -i "{target_file}" -vn -c:a libmp3lame -q:a 2 "{output}"'

    if output_ext in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        return f'ffmpeg -i "{target_file}" "{output}"'

    if output_ext in {".mp4", ".mkv", ".mov", ".webm"}:
        if (encoder_family or "").lower() == "nvidia":
            return f'ffmpeg -i "{target_file}" -c:v h264_nvenc -preset fast -cq 28 -c:a aac -b:a 160k "{output}"'
        return f'ffmpeg -i "{target_file}" -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 160k "{output}"'

    return f'ffmpeg -i "{target_file}" "{output}"'

def _build_share_compress_fallback_command(query, target_file, encoder_family):
    output_name = None
    for ext in (".mp4", ".mkv", ".mov", ".webm", ".mp3", ".aac", ".wav"):
        output_name = _extract_output_name_from_query(query, ext)
        if output_name:
            break
    if not output_name:
        output_name = _suggest_output_name(target_file, "share", ".mp4")

    media_type = utils.get_media_type(target_file)
    if media_type == "audio":
        ext = Path(output_name).suffix.lower()
        if ext == ".wav":
            return f'ffmpeg -i "{target_file}" -vn -c:a pcm_s16le "{output_name}"'
        if ext == ".aac":
            return f'ffmpeg -i "{target_file}" -vn -c:a aac -b:a 160k "{output_name}"'
        return f'ffmpeg -i "{target_file}" -vn -c:a libmp3lame -q:a 3 "{output_name}"'

    vf_parts = []
    ql = query.lower()
    if "scale to 1080p" in ql:
        vf_parts.append("scale=-2:1080")
    elif "scale to 720p" in ql:
        vf_parts.append("scale=-2:720")
    if "use 60 fps" in ql:
        vf_parts.append("fps=60")
    elif "use 30 fps" in ql:
        vf_parts.append("fps=30")
    vf_opt = f' -vf "{",".join(vf_parts)}"' if vf_parts else ""

    if "stronger compression" in ql:
        cpu_quality = "-crf 28"
        nv_quality = "-cq 32"
    elif "higher-quality compression" in ql:
        cpu_quality = "-crf 20"
        nv_quality = "-cq 24"
    else:
        cpu_quality = "-crf 23"
        nv_quality = "-cq 28"

    if (encoder_family or "").lower() == "nvidia":
        return (
            f'ffmpeg -i "{target_file}"{vf_opt} '
            f'-c:v h264_nvenc -preset fast {nv_quality} -c:a aac -b:a 160k "{output_name}"'
        )
    return (
        f'ffmpeg -i "{target_file}"{vf_opt} '
        f'-c:v libx264 -preset veryfast {cpu_quality} -c:a aac -b:a 160k "{output_name}"'
    )

def _build_deterministic_fallback_command(query, target_file, encoder_family):
    ql = query.lower()
    media_type = utils.get_media_type(target_file)

    if "animated gif" in ql and media_type == "video":
        return _build_gif_fallback_command(query, target_file)
    if "webp" in ql and media_type == "image":
        return _build_webp_fallback_command(query, target_file)
    if "extract audio only" in ql and media_type == "video":
        return _build_extract_audio_fallback_command(query, target_file)
    if "remove audio from" in ql and media_type == "video":
        return _build_remove_audio_fallback_command(query, target_file)
    if "resize" in ql and media_type == "image":
        return _build_resize_image_fallback_command(query, target_file)
    if "convert" in ql and "save as" in ql:
        converted = _build_change_format_fallback_command(query, target_file, encoder_family)
        if converted:
            return converted
    if "compress" in ql and "save as" in ql:
        return _build_share_compress_fallback_command(query, target_file, encoder_family)
    return None


def _render_welcome(args):
    dry_run_label = "ON" if args.dry_run else "OFF"
    ultra_safe_label = "ON" if args.ultra_safe else "OFF"
    
    banner_large = r"""
  ____  _____  _____ _   _ __  __ _____ ____ ___    _    
 / __ \|  __ \|  ___| \ | |  \/  | ____|  _ \_ _|  / \   
| |  | | |__) | |__ |  \| | |\/| |  _| | | | | |  / _ \  
| |__| |  ___/|  ___| |\  | |  | | |___| |_| | | / ___ \ 
 \____/|_|    |_____|_| \_|_|  |_|_____|____/___/_/   \_\
                                                         
  ____ _     ___ 
 / ___| |   |_ _|
| |   | |    | | 
| |___| |___ | | 
 \____|_____|___|
"""
    banner_small = "[bold cyan]OPENMEDIA CLI v1.1.0[/]"

    # Metasploit style stats
    stats_lines = [
        f"=[ [bold white]openmedia v1.1.0-stable[/] ]",
        f"+ -- --=[ [cyan]Model:[/] Qwen2.5-Coder:7B (Local/Ollama) ]",
        f"+ -- --=[ [cyan]Enablers:[/] FFmpeg, LangGraph, Rich ]",
        f"+ -- --=[ [cyan]System:[/] dry-run={dry_run_label}, ultra-safe={ultra_safe_label} ]",
    ]
    
    if console.width < 70:
        console.print(banner_small)
    else:
        console.print(Text(banner_large, style="bold cyan"))

    for line in stats_lines:
        console.print(line)
    console.print()

    # Instructions in metasploit style
    instructions = (
        "      [bold white]Usage Instructions:[/]\n\n"
        "      1. Select a media file from the local directory or provide a path.\n"
        "      2. Choose a transformation task or provide a custom request.\n"
        "      3. Review the AI-generated FFmpeg command.\n"
        "      4. Confirm execution to process your media safely.\n\n"
        "      [dim]Note: Ensure Ollama is running with the required model installed.[/]"
    )
    console.print(Panel(instructions, border_style="bright_black", expand=False))
    console.print()


def main(argv=None):
    args = parse_args(argv)
    
    # Handle 'info' command
    if args.command == "info":
        if not Path(args.path).exists():
            console.print(f"[bold red]X File not found:[/] {args.path}")
            return
        display_media_info(args.path)
        return

    console.clear()
    _render_welcome(args)

    if not check_ollama():
        console.print("[bold red]X Ollama is not running. Please start Ollama first.[/]")
        return
    
    if not check_ollama_model_available():
        console.print(
            "[bold yellow]⚠ Warning: Required model 'qwen2.5-coder:7b-instruct-q4_K_M' not found.[/]\n"
            "[yellow]Run: ollama pull qwen2.5-coder:7b-instruct-q4_K_M[/]"
        )
        if not questionary.confirm("Continue anyway?", style=OM_STYLE, default=False).ask():
            return

    if args.settings:
        open_settings_menu()
        return

    target_file = _select_target_file()
    if not target_file:
        return

    # Show technical info table before asking for request
    display_media_info(target_file)
    console.print()

    query = _collect_user_request(target_file)
    if not query:
        return

    console.print(
        Panel(
            f"[bold]Selected file:[/] {target_file}\n[bold]Requested edit:[/] {query}",
            title="Review Request",
            border_style="blue",
            style="on grey23",
            expand=False
        )
    )

    encoding_preference, effective_encoder_family, nvenc_available = _resolve_encoding_mode()
    if not encoding_preference:
        return

    preferred_video_encoder = "h264_nvenc" if effective_encoder_family == "nvidia" else "libx264"

    llm_runtime_mode, llm_effective_mode = _resolve_llm_mode(
        effective_encoder_family, nvenc_available
    )
    if not llm_runtime_mode:
        return

    console.print(
        f"[cyan]Ollama inference mode: {llm_effective_mode.upper()} "
        "(model unloads after each generation).[/]"
    )
    if args.ultra_safe:
        console.print("[cyan]Ultra-safe mode enabled: lower CPU/LLM load tuning is active.[/]")

    media_context = utils.build_media_context(target_file)

    with console.status("[bold green]Agent is thinking, validating, and optimizing..."):
        initial_state = AgentState(
            user_input=query,
            target_file=target_file,
            media_context=media_context,
            ultra_safe=args.ultra_safe,
            encoding_preference=encoding_preference,
            llm_runtime_mode=llm_runtime_mode,
            llm_effective_mode=llm_effective_mode,
            effective_encoder_family=effective_encoder_family,
            preferred_video_encoder=preferred_video_encoder,
            nvenc_available=nvenc_available,
            iteration_count=0,
            history=[],
        )
        final_state = media_agent.invoke(initial_state)

    if final_state.get("is_valid") and final_state.get("generated_command"):
        cmd = final_state["generated_command"]
        prepared_cmd, was_tuned = executor.prepare_command_for_safe_execution(
            cmd, effective_encoder_family, target_file, args.ultra_safe
        )

        console.print(
            Panel(
                f"[yellow]{prepared_cmd}[/]",
                title="[bold green]AI Recommended Command[/]",
                subtitle=f"Generated in {final_state['iteration_count']} attempt(s)",
                expand=False
            )
        )

        if was_tuned:
            console.print(
                "[cyan]Safety mode adjusted naming/encoder/thread settings for smoother execution.[/]"
            )

        if args.dry_run:
            console.print(
                "[cyan]Dry-run mode enabled: command was generated and validated but not executed.[/]"
            )
            return

        if questionary.confirm("Execute this command?", style=OM_STYLE).ask():
            with console.status("[bold blue]Rendering media via FFmpeg...[/]"):
                success, msg = executor.run_ffmpeg(
                    cmd,
                    query,
                    effective_encoder_family,
                    target_file,
                    args.ultra_safe,
                )

            if success:
                console.print("[bold green]Success: Process complete.[/]")
            else:
                console.print(f"[bold red]FFmpeg Error:[/]\n{msg}")
                fallback_cmd = _build_deterministic_fallback_command(
                    query, target_file, effective_encoder_family
                )
                if fallback_cmd and fallback_cmd.strip() != cmd.strip():
                    audited_fallback = _audit_command_for_execution(
                        fallback_cmd,
                        query,
                        target_file,
                        media_context,
                        effective_encoder_family,
                    )
                    if audited_fallback.is_valid and audited_fallback.generated_command:
                        safe_fallback_cmd = audited_fallback.generated_command
                        prepared_fallback, _ = executor.prepare_command_for_safe_execution(
                            safe_fallback_cmd, effective_encoder_family, target_file, args.ultra_safe
                        )
                        console.print(
                            Panel(
                                f"[yellow]{prepared_fallback}[/]",
                                title="[bold yellow]Deterministic Fallback Available[/]",
                                subtitle="Primary command failed; fallback is generated from preset intent",
                                expand=False,
                            )
                        )
                        if questionary.confirm("Retry using fallback command?", style=OM_STYLE).ask():
                            with console.status("[bold blue]Rendering media via FFmpeg (fallback)...[/]"):
                                fb_success, fb_msg = executor.run_ffmpeg(
                                    safe_fallback_cmd,
                                    query,
                                    effective_encoder_family,
                                    target_file,
                                    args.ultra_safe,
                                )
                            if fb_success:
                                console.print("[bold green]Success: Process complete via fallback.[/]")
                            else:
                                console.print(f"[bold red]Fallback FFmpeg Error:[/]\n{fb_msg}")
                    else:
                        reason = audited_fallback.error_message or "Fallback failed safety validation."
                        console.print(f"[bold red]Fallback blocked by safety checks:[/]\n{reason}")
    else:
        fallback_cmd = None
        if target_file and query:
            fallback_cmd = _build_deterministic_fallback_command(
                query, target_file, effective_encoder_family
            )

        if fallback_cmd:
            audited_fallback = _audit_command_for_execution(
                fallback_cmd,
                query,
                target_file,
                media_context,
                effective_encoder_family,
            )
            if not audited_fallback.is_valid or not audited_fallback.generated_command:
                reason = audited_fallback.error_message or "Fallback failed safety validation."
                console.print(f"[bold red]Fallback blocked by safety checks:[/]\n{reason}")
                return

            safe_fallback_cmd = audited_fallback.generated_command
            prepared_fallback, was_tuned = executor.prepare_command_for_safe_execution(
                safe_fallback_cmd, effective_encoder_family, target_file, args.ultra_safe
            )
            console.print(
                Panel(
                    f"[yellow]{prepared_fallback}[/]",
                    title="[bold yellow]Fallback Command[/]",
                    subtitle="LLM command retries were unsafe; using deterministic fallback",
                    expand=False
                )
            )

            if was_tuned:
                console.print(
                    "[cyan]Safety mode adjusted naming/encoder/thread settings for smoother execution.[/]"
                )

            if args.dry_run:
                console.print(
                    "[cyan]Dry-run mode enabled: fallback command was prepared but not executed.[/]"
                )
                return

            if questionary.confirm("Execute fallback command?", style=OM_STYLE).ask():
                with console.status("[bold blue]Rendering media via FFmpeg...[/]"):
                    success, msg = executor.run_ffmpeg(
                        safe_fallback_cmd,
                        query,
                        effective_encoder_family,
                        target_file,
                        args.ultra_safe,
                    )

                if success:
                    console.print("[bold green]Success: Process complete.[/]")
                else:
                    console.print(f"[bold red]FFmpeg Error:[/]\n{msg}")
            return

        error = final_state.get("error_message", "Unknown logic error.")
        console.print(
            Panel(
                f"[bold red]Agent failed to generate a safe command:[/]\n{error}",
                title="Process Failed",
                expand=False
            )
        )


if __name__ == "__main__":
    main()
