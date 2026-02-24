import os

VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.mov', '.avi', '.mp3', '.wav', '.flac')

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