import subprocess
from openmedia.utils import log_command

def run_ffmpeg(command, user_prompt):
    try:
        # We use shell=True because FFmpeg commands often use pipes or complex strings
        result = subprocess.run(command, shell=True, check=True)
        
        # If it reaches here, it was successful
        log_command(user_prompt, command, "SUCCESS")
        return True
        
    except subprocess.CalledProcessError as e:
        # If FFmpeg fails, we log the failure too
        log_command(user_prompt, command, f"FAILED (Exit Code: {e.returncode})")
        print(f"\n[!] FFmpeg Error: {e}")
        return False