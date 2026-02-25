import argparse

import questionary
import requests
from rich.console import Console
from rich.panel import Panel

from openmedia.graph import media_agent
from openmedia.state import AgentState
from . import executor, utils

console = Console()


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
        ).ask()

        if not choice or choice == "Back":
            return

        if choice.startswith("Encoding mode"):
            encoding = questionary.select(
                "Default encoding mode:",
                choices=["auto", "nvidia", "cpu"],
            ).ask()
            if encoding:
                utils.set_encoding_preference(encoding)
                console.print(f"[green]Saved encoding mode: {encoding}[/]")
            continue

        if choice.startswith("Ollama mode"):
            llm_mode = questionary.select(
                "Default Ollama runtime mode:",
                choices=["auto", "gpu", "cpu"],
            ).ask()
            if llm_mode:
                utils.set_llm_runtime_mode(llm_mode)
                console.print(f"[green]Saved Ollama mode: {llm_mode}[/]")
            continue

        if choice == "Reset all saved settings":
            confirmed = questionary.confirm("Reset all saved settings?").ask()
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


def main(argv=None):
    args = parse_args(argv)
    console.clear()
    console.print(
        Panel(
            "[bold cyan]OpenMedia AI[/]\n[italic]Agentic Local Edition (Phase 2)[/]",
            expand=False,
            border_style="cyan",
        )
    )

    if not check_ollama():
        console.print("[bold red]X Ollama is not running. Please start Ollama first.[/]")
        return

    if args.settings:
        open_settings_menu()
        return

    query = questionary.text("What should we do? (or type /settings)").ask()
    if not query:
        return
    if query.strip().lower() == "/settings":
        open_settings_menu()
        return

    local_files = utils.get_local_media_files()
    target_file = utils.find_best_match(query, local_files)

    if not target_file and local_files:
        target_file = questionary.select(
            "I couldn't identify the file. Which one do you want to edit?",
            choices=local_files + ["None / Type manually"],
        ).ask()

    if target_file == "None / Type manually":
        target_file = questionary.text("Enter filename:").ask()

    if not target_file:
        return

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

        if questionary.confirm("Execute this command?").ask():
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


if __name__ == "__main__":
    main()
