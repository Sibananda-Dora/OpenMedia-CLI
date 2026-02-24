import requests
import questionary
from rich.console import Console
from rich.panel import Panel
from . import utils, brain, executor

console = Console()

def check_ollama():
    """Ping Ollama to ensure the server is running."""
    try:
        requests.get("http://localhost:11434/", timeout=2)
        return True
    except requests.exceptions.ConnectionError:
        return False

def main():
    console.print(Panel("[bold cyan]OpenMedia AI[/]\n[italic]Your Local FFmpeg Assistant[/]", expand=False))

    if not check_ollama():
        console.print("[bold red]✘ Ollama is not running! Please start the Ollama app first.[/]")
        return

    # 1. Get Input
    query = questionary.text("What should we do?").ask()
    if not query: 
        return

    # 2. Resolve Files
    local_files = utils.get_local_media_files()
    target_file = utils.find_best_match(query, local_files)

    if not target_file and local_files:
        target_file = questionary.select(
            "I couldn't identify the file. Which one do you want to edit?",
            choices=local_files + ["None / Type manually"]
        ).ask()

    # 3. Get AI Command
    with console.status("[bold green]Consulting AI...") as status:
        cmd = brain.generate_command(query, target_file)

    if cmd.startswith("Error"):
        console.print(f"[bold red]{cmd}[/]")
        return

    # 4. Confirm & Execute
    console.print(Panel(f"[yellow]{cmd}[/]", title="Generated Command"))
    
    if questionary.confirm("Execute this command?").ask():
        with console.status("[bold blue]Rendering media via FFmpeg...[/]"):
            success, msg = executor.run_ffmpeg(cmd)
            
        if success:
            console.print("[bold green]✔ Done![/]")
        else:
            console.print(f"[bold red]✘ Failed:[/]\n{msg}")

if __name__ == "__main__":
    main()