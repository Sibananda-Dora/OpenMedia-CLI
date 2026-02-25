import argparse
from pathlib import Path

import questionary
import requests
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from questionary import Style

from openmedia.graph import media_agent
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
    except requests.exceptions.ConnectionError:
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
        console_print(
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

    while True:
        action = questionary.select(
            "What would you like to do?",
            choices=[
                "Compress for sharing (Recommended)",
                "Change file format",
                "Extract audio only",
                "Remove audio from video",
                "Custom request",
                "Open settings",
                "Cancel",
            ],
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

        if action == "Compress for sharing (Recommended)":
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

        if action == "Change file format":
            target_format = questionary.select(
                "Choose output format:",
                choices=["mp4", "mkv", "mov", "webm"],
                style=OM_STYLE
            ).ask()
            output_name = _ask_output_name(
                "Output filename:",
                _suggest_output_name(target_file, "converted", f".{target_format}"),
            )
            return (
                f"Convert {target_name} to {target_format.upper()} with good visual quality and "
                f"proper audio sync. Save as {output_name}."
            )

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
    console.clear()
    _render_welcome(args)

    if not check_ollama():
        console.print("[bold red]X Ollama is not running. Please start Ollama first.[/]")
        return

    if args.settings:
        open_settings_menu()
        return

    target_file = _select_target_file()
    if not target_file:
        return

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
                expand=False
            )
        )


if __name__ == "__main__":
    main()
