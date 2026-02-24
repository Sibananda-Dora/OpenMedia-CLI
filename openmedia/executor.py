import subprocess

def run_ffmpeg(command):
    """Executes the FFmpeg command safely."""
    try:
        # capture_output keeps the terminal clean while FFmpeg works
        process = subprocess.run(
            command, 
            shell=True, 
            check=True, 
            capture_output=True, 
            text=True
        )
        return True, "Success"
    except subprocess.CalledProcessError as e:
        # If FFmpeg crashes, this grabs the exact error message
        return False, e.stderr