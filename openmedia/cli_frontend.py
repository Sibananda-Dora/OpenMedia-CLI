import argparse
import re
from pathlib import Path
import questionary
import requests
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from questionary import Style

from openmedia.graph import media_agent
from openmedia.nodes import validator_node, safety_reviewer_node
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


def check_ollama():
    """Ping Ollama to ensure the server is running."""
    try:
        requests.get("http://localhost:11434/", timeout=2)
        return True
    except requests.exceptions.RequestException:
        return False


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
            style=OM_STYLE,
        ).ask()

        if not choice or choice == "Back":
            return

        if choice.startswith("Encoding mode"):
            encoding = questionary.select(
                "Default encoding mode:",
                choices=["auto", "nvidia", "cpu"],
                style=OM_STYLE,
            ).ask()
            if encoding:
                utils.set_encoding_preference(encoding)
                console.print(f"[green]Saved encoding mode: {encoding}[/]")
            continue

        if choice.startswith("Ollama mode"):
            llm_mode = questionary.select(
                "Default Ollama runtime mode:",
                choices=["auto", "gpu", "cpu"],
                style=OM_STYLE,
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
            style=OM_STYLE,
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


def _resolve_llm_mode(nvenc_available):
    llm_runtime_mode = utils.get_llm_runtime_mode()
    if llm_runtime_mode not in {"auto", "gpu", "cpu"}:
        llm_runtime_mode = None

    if not llm_runtime_mode:
        llm_runtime_mode = questionary.select(
            "Choose Ollama runtime mode (saved for future runs):",
            choices=["auto", "gpu", "cpu"],
            style=OM_STYLE,
        ).ask()
        if not llm_runtime_mode:
            return None, None
        utils.set_llm_runtime_mode(llm_runtime_mode)

    llm_effective_mode = llm_runtime_mode
    if llm_runtime_mode == "auto":
        # Use NVENC detection as a pragmatic GPU capability signal.
        # If we cannot detect NVIDIA support, default to CPU for stability.
        llm_effective_mode = "gpu" if nvenc_available else "cpu"
    elif llm_runtime_mode == "gpu" and not nvenc_available:
        console.print(
            "[yellow]GPU mode requested for Ollama but no NVIDIA GPU support detected. "
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
            style=OM_STYLE,
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


# Sentinel returned by _collect_user_request when the user wants to go
# back and pick a different file instead of proceeding with this one.
_BACK_TO_FILE_SELECT = "__BACK_TO_FILE_SELECT__"


def _collect_user_request(target_file):
    target_name = Path(target_file).name

    while True:
        action = questionary.select(
            "What do you want to do?",
            choices=[
                "Compress for sharing (Recommended)",
                "Change file format",
                "Extract audio only",
                "Remove audio from video",
                "Custom request",
                "Open settings",
                "⮜ Back (change file)",
                "Cancel",
            ],
            style=OM_STYLE,
        ).ask()

        if not action or action == "Cancel":
            return None

        if action == "⮜ Back (change file)":
            return _BACK_TO_FILE_SELECT

        if action == "Open settings":
            open_settings_menu()
            continue

        if action == "Custom request":
            query = questionary.text(
                "Describe your edit request:",
                default=f"Optimize {target_name} for sharing while preserving quality.",
                style=OM_STYLE,
            ).ask()
            if not query:
                return None
            if query.strip().lower() == "/settings":
                open_settings_menu()
                continue
            return query

        # --- Compress for sharing ---
        if action == "Compress for sharing (Recommended)":
            quality_choice = questionary.select(
                "Compression target:",
                choices=[
                    "Balanced size and quality (Recommended)",
                    "Smaller file size",
                    "Higher quality",
                    "⮜ Back",
                ],
                style=OM_STYLE,
            ).ask()
            if not quality_choice or quality_choice == "⮜ Back":
                continue

            resolution_choice = questionary.select(
                "Output resolution:",
                choices=["Keep original (Recommended)", "1080p", "720p", "⮜ Back"],
                style=OM_STYLE,
            ).ask()
            if not resolution_choice or resolution_choice == "⮜ Back":
                continue

            fps_choice = questionary.select(
                "Frame rate:",
                choices=["Keep original (Recommended)", "60 fps", "30 fps", "⮜ Back"],
                style=OM_STYLE,
            ).ask()
            if not fps_choice or fps_choice == "⮜ Back":
                continue

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

        # --- Change file format ---
        if action == "Change file format":
            target_format = questionary.select(
                "Choose output format:",
                choices=["mp4", "mkv", "mov", "webm", "⮜ Back"],
                style=OM_STYLE,
            ).ask()
            if not target_format or target_format == "⮜ Back":
                continue

            output_name = _ask_output_name(
                "Output filename:",
                _suggest_output_name(target_file, "converted", f".{target_format}"),
            )
            return (
                f"Convert {target_name} to {target_format.upper()} with good visual quality and "
                f"proper audio sync. Save as {output_name}."
            )

        # --- Extract audio only ---
        if action == "Extract audio only":
            audio_format = questionary.select(
                "Choose audio format:",
                choices=["mp3", "aac", "wav", "⮜ Back"],
                style=OM_STYLE,
            ).ask()
            if not audio_format or audio_format == "⮜ Back":
                continue

            output_name = _ask_output_name(
                "Output filename:",
                _suggest_output_name(target_file, "audio", f".{audio_format}"),
            )
            return (
                f"Extract audio only from {target_name} and save as {output_name}. "
                f"Keep clear listening quality."
            )

        # --- Remove audio from video ---
        if action == "Remove audio from video":
            confirm = questionary.confirm(
                f"Remove audio from {target_name}?", style=OM_STYLE
            ).ask()
            if not confirm:
                continue

            output_name = _ask_output_name(
                "Output filename:",
                _suggest_output_name(target_file, "muted", Path(target_file).suffix or ".mp4"),
            )
            return f"Remove audio from {target_name} while keeping video quality. Save as {output_name}."


def _display_media_info(target_file):
    """Probes the file and displays a Rich table with media metadata."""
    metadata, error = utils.probe_media_file(target_file)
    if error or metadata is None:
        console.print(f"[dim]Media info: {error or 'unavailable'}[/]")
        return

    fmt = metadata.get("format", {})
    streams = metadata.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

    table = Table(title="Media Info", border_style="bright_black", expand=False)
    table.add_column("Property", style="cyan", no_wrap=True)
    table.add_column("Value", style="white")

    # Format
    table.add_row("Container", fmt.get("format_name", "unknown"))

    # Duration
    raw_dur = fmt.get("duration")
    if raw_dur:
        try:
            secs = float(raw_dur)
            mins, sec = divmod(int(secs), 60)
            hrs, mins = divmod(mins, 60)
            dur_str = f"{hrs:02d}:{mins:02d}:{sec:02d}" if hrs else f"{mins:02d}:{sec:02d}"
        except ValueError:
            dur_str = raw_dur
    else:
        dur_str = "unknown"
    table.add_row("Duration", dur_str)

    # Size
    raw_size = fmt.get("size")
    table.add_row("File Size", utils.format_size(raw_size) if raw_size else "unknown")

    # Bitrate
    raw_br = fmt.get("bit_rate")
    table.add_row("Bitrate", utils.format_bitrate(raw_br) if raw_br else "unknown")

    # Video stream
    if video_stream:
        table.add_row("Video Codec", video_stream.get("codec_name", "none"))
        w = video_stream.get("width", "?")
        h = video_stream.get("height", "?")
        table.add_row("Resolution", f"{w}x{h}")
        table.add_row("Frame Rate", video_stream.get("r_frame_rate", "unknown"))

    # Audio stream
    if audio_stream:
        table.add_row("Audio Codec", audio_stream.get("codec_name", "none"))
        sr = audio_stream.get("sample_rate", "unknown")
        ch = audio_stream.get("channels", "unknown")
        table.add_row("Sample Rate", f"{sr} Hz" if sr != "unknown" else sr)
        table.add_row("Channels", str(ch))

    console.print(table)
    console.print()


def _render_welcome(args):
    dry_run_label = "ON" if args.dry_run else "OFF"
    ultra_safe_label = "ON" if args.ultra_safe else "OFF"
    
    banner = r"""
[bold cyan]
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
[/]"""

    # Metasploit style stats
    stats = [
        f"=[ [bold white]openmedia v1.1.0-stable[/] ]",
        f"+ -- --=[ [cyan]Model:[/] Qwen2.5-Coder:7B (Local/Ollama) ]",
        f"+ -- --=[ [cyan]Enablers:[/] FFmpeg, LangGraph, Rich ]",
        f"+ -- --=[ [cyan]System:[/] dry-run={dry_run_label}, ultra-safe={ultra_safe_label} ]",
    ]
    
    console.print(banner)
    for line in stats:
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
    console.clear()
    _render_welcome(args)

    if not check_ollama():
        console.print("[bold red]X Ollama is not running. Please start Ollama first.[/]")
        return

    if not utils.check_ollama_model():
        console.print(
            "[yellow]⚠ Model 'qwen2.5-coder:7b-instruct-q4_K_M' not found in Ollama.\n"
            "  Run: ollama pull qwen2.5-coder:7b-instruct-q4_K_M[/]"
        )

    if args.settings:
        open_settings_menu()
        return

    # ── Outer loop: allows the user to go back from task selection
    #    to file selection.
    while True:
        console.print(Panel("[bold]Step 1/3: Media Selection[/]", style="on grey23", border_style="bright_black"))
        target_file = _select_target_file()
        if not target_file:
            return

        _display_media_info(target_file)

        console.print(Panel("[bold]Step 2/3: Transformation Task[/]", style="on grey23", border_style="bright_black"))
        query = _collect_user_request(target_file)
        if not query:
            return
        if query == _BACK_TO_FILE_SELECT:
            console.print("[dim]Returning to file selection…[/]\n")
            continue
        break  # user picked a valid task — proceed

    console.print(
        Panel(
            f"[bold]Selected file:[/] {target_file}\n[bold]Requested edit:[/] {query}",
            title="Step 3/3: Review",
            border_style="blue",
            style="on grey23"
        )
    )

    encoding_preference, effective_encoder_family, nvenc_available = _resolve_encoding_mode()
    if not encoding_preference:
        return

    preferred_video_encoder = "h264_nvenc" if effective_encoder_family == "nvidia" else "libx264"

    llm_runtime_mode, llm_effective_mode = _resolve_llm_mode(nvenc_available)
    if not llm_runtime_mode:
        return

    console.print(
        f"[cyan]Ollama inference mode: {(llm_effective_mode or 'cpu').upper()} "
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
                    prepared_cmd,
                    query,
                    effective_encoder_family,
                    target_file,
                    args.ultra_safe,
                )

            if success:
                console.print("[bold green]Success: Process complete.[/]")
            else:
                console.print(f"[bold red]FFmpeg Error:[/]\n{msg}")
    else:
        error = final_state.get("error_message", "Unknown logic error.")
        console.print(
            Panel(
                f"[bold red]Agent failed to generate a safe command:[/]\n{error}",
                title="Process Failed",
            )
        )

        # --- Deterministic fallback: try a hardcoded command ---
        fallback_cmd = _build_deterministic_fallback_command(
            query, target_file, effective_encoder_family
        )
        if fallback_cmd:
            console.print("[cyan]Attempting deterministic fallback command...[/]")
            audited = _audit_command_for_execution(
                fallback_cmd, query, target_file, media_context, effective_encoder_family
            )
            if audited.is_valid and audited.generated_command:
                prepared_fb, fb_tuned = executor.prepare_command_for_safe_execution(
                    audited.generated_command, effective_encoder_family, target_file, args.ultra_safe
                )
                console.print(
                    Panel(
                        f"[yellow]{prepared_fb}[/]",
                        title="[bold cyan]Fallback Command[/]",
                        subtitle="Generated without LLM",
                    )
                )
                if not args.dry_run and questionary.confirm(
                    "Execute this fallback command?", style=OM_STYLE
                ).ask():
                    with console.status("[bold blue]Rendering media via FFmpeg...[/]"):
                        success, msg = executor.run_ffmpeg(
                            prepared_fb, query, effective_encoder_family,
                            target_file, args.ultra_safe,
                        )
                    if success:
                        console.print("[bold green]Success: Process complete.[/]")
                    else:
                        console.print(f"[bold red]FFmpeg Error:[/]\n{msg}")


def _extract_output_name_from_query(query):
    """Extracts 'save as <name>' from a user query, returns the name or None."""
    match = re.search(
        r"\bsave as\s+(?:\"([^\"]+)\"|'([^']+)'|([^\n]+))",
        query,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    raw_name = next((part for part in match.groups() if part), "")
    candidate = raw_name.strip().rstrip(" .,:;!?")
    return candidate or None


def _build_deterministic_fallback_command(query, target_file, encoder_family):
    """
    Builds a deterministic (non-LLM) FFmpeg command for common tasks.
    Returns None if the query doesn't match any known pattern.
    """
    lower = query.lower()
    output_name = _extract_output_name_from_query(query)
    family = (encoder_family or "").lower()
    video_output_exts = {".mp4", ".mkv", ".mov", ".webm", ".avi"}
    image_output_exts = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}

    # --- Extract audio only ---
    if "extract audio" in lower or ("extract" in lower and "audio" in lower):
        if not output_name:
            stem = Path(target_file).stem
            output_name = f"{stem}_audio.mp3"
        ext = Path(output_name).suffix.lower()
        if ext in (".mp3",):
            return f'ffmpeg -i "{target_file}" -vn -c:a libmp3lame -q:a 2 "{output_name}"'
        if ext in (".aac",):
            return f'ffmpeg -i "{target_file}" -vn -c:a aac -b:a 192k "{output_name}"'
        if ext in (".wav",):
            return f'ffmpeg -i "{target_file}" -vn -c:a pcm_s16le "{output_name}"'
        return f'ffmpeg -i "{target_file}" -vn -c:a libmp3lame -q:a 2 "{output_name}"'

    # --- Remove audio ---
    if "remove audio" in lower or "mute" in lower:
        if not output_name:
            stem = Path(target_file).stem
            suffix = Path(target_file).suffix or ".mp4"
            output_name = f"{stem}_muted{suffix}"
        if family == "nvidia":
            return f'ffmpeg -i "{target_file}" -an -c:v h264_nvenc -preset fast -cq 28 "{output_name}"'
        return f'ffmpeg -i "{target_file}" -an -c:v libx264 -preset veryfast -crf 23 "{output_name}"'

    # --- GIF to WebP (lossless) ---
    if "webp" in lower and ("lossless" in lower or "perfect" in lower):
        if not output_name:
            stem = Path(target_file).stem
            output_name = f"{stem}_optimized.webp"
        return f'ffmpeg -i "{target_file}" -c:v libwebp -lossless 1 "{output_name}"'

    # --- Compress / share ---
    if "compress" in lower or "sharing" in lower:
        if not output_name:
            stem = Path(target_file).stem
            output_name = f"{stem}_share.mp4"

        vf_parts = []
        if "720p" in lower:
            vf_parts.append("scale=-2:720")
        elif "1080p" in lower:
            vf_parts.append("scale=-2:1080")
        if "30 fps" in lower:
            vf_parts.append("fps=30")
        elif "60 fps" in lower:
            vf_parts.append("fps=60")

        vf_str = f' -vf "{",".join(vf_parts)}"' if vf_parts else ""

        if family == "nvidia":
            return f'ffmpeg -i "{target_file}"{vf_str} -c:v h264_nvenc -preset fast -cq 28 -c:a aac -b:a 128k "{output_name}"'
        return f'ffmpeg -i "{target_file}"{vf_str} -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 128k "{output_name}"'

    # --- Format conversion (audio) ---
    if "convert" in lower:
        if output_name:
            ext = Path(output_name).suffix.lower()
            if ext == ".mp3":
                return f'ffmpeg -i "{target_file}" -c:a libmp3lame -q:a 2 "{output_name}"'
            if ext == ".aac":
                return f'ffmpeg -i "{target_file}" -c:a aac -b:a 192k "{output_name}"'
            if ext == ".wav":
                return f'ffmpeg -i "{target_file}" -c:a pcm_s16le "{output_name}"'
            if ext in image_output_exts:
                # Deterministic fallback intentionally avoids image conversion
                # heuristics to prevent wrong or surprising outputs.
                return None
            if ext in video_output_exts and family == "nvidia":
                return f'ffmpeg -i "{target_file}" -c:v h264_nvenc -preset fast -cq 28 -c:a aac "{output_name}"'
            if ext in video_output_exts:
                return f'ffmpeg -i "{target_file}" -c:v libx264 -preset veryfast -crf 23 -c:a aac "{output_name}"'
            return None

    return None


def _audit_command_for_execution(command, user_input, target_file, media_context, encoder_family):
    """
    Runs the safety pipeline (validator → reviewer) on a pre-built command.
    Returns the final AgentState.
    """
    state = AgentState(
        user_input=user_input,
        target_file=target_file,
        media_context=media_context,
        effective_encoder_family=encoder_family,
        generated_command=command,
        is_valid=True,  # assume valid, let validator decide
    )
    state = validator_node(state)
    if not state.is_valid:
        return state
    state = safety_reviewer_node(state)
    return state


if __name__ == "__main__":
    main()
