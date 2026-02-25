import requests
import questionary
from rich.console import Console
from rich.panel import Panel
from rich.live import Live

# Phase 2 Imports
from openmedia.state import AgentState
from openmedia.graph import media_agent
from . import utils, executor

console = Console()

def check_ollama():
    """Ping Ollama to ensure the server is running."""
    try:
        requests.get("http://localhost:11434/", timeout=2)
        return True
    except requests.exceptions.ConnectionError:
        return False

def main():
    console.clear()
    console.print(Panel(
        "[bold cyan]MediaHandle AI[/]\n[italic]Agentic Local Edition (Phase 2)[/]", 
        expand=False, 
        border_style="cyan"
    ))

    if not check_ollama():
        console.print("[bold red]✘ Ollama is not running! Please start the Ollama app first.[/]")
        return

    # 1. Get Input
    query = questionary.text("What should we do? (e.g., 'Make it 720p silent')").ask()
    if not query: 
        return

    # 2. Resolve Files (Keeping your original logic)
    local_files = utils.get_local_media_files()
    target_file = utils.find_best_match(query, local_files)

    if not target_file and local_files:
        target_file = questionary.select(
            "I couldn't identify the file. Which one do you want to edit?",
            choices=local_files + ["None / Type manually"]
        ).ask()
    
    # Fallback for manual entry
    if target_file == "None / Type manually":
        target_file = questionary.text("Enter filename:").ask()
    
    if not target_file:
        return

    # 3. Phase 2: Invoke the Agentic Graph
    # We wrap the thinking process in a status spinner
    with console.status("[bold green]Agent is thinking, validating, and optimizing...") as status:
        # Initialize the Pydantic State
        initial_state = AgentState(
            user_input=query,
            target_file=target_file,
            iteration_count=0,
            history=[]
        )
        
        # Run the LangGraph agent
        final_state = media_agent.invoke(initial_state)

    # 4. Handle Agent Output
    if final_state.get('is_valid') and final_state.get('generated_command'):
        cmd = final_state['generated_command']
        
        console.print(Panel(
            f"[yellow]{cmd}[/]", 
            title="[bold green]AI Recommended Command[/]", 
            subtitle=f"Generated in {final_state['iteration_count']} attempt(s)"
        ))
        
        # 5. Confirm & Execute
        if questionary.confirm("Execute this command?").ask():
            with console.status("[bold blue]Rendering media via FFmpeg...[/]"):
                # Pass the original query to the executor for high-quality logging
                success, msg = executor.run_ffmpeg(cmd, query)
                
            if success:
                console.print("[bold green]✔ Success: Process complete.[/]")
            else:
                console.print(f"[bold red]✘ FFmpeg Error:[/]\n{msg}")
    else:
        # If the agent failed validation or hit the iteration limit
        error = final_state.get('error_message', 'Unknown logic error.')
        console.print(Panel(f"[bold red]Agent failed to generate a safe command:[/]\n{error}", title="Process Failed"))

if __name__ == "__main__":
    main()