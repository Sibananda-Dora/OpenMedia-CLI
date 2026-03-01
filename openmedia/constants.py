import shlex

FORBIDDEN_TOKENS = {
    "rm",
    "del",
    "erase",
    "powershell",
    "cmd",
    "bash",
    "sh",
    "python",
    "curl",
    "wget",
    "invoke-webrequest",
}

SHELL_OPERATORS = {"|", "||", "&&", ";", "&"}
REDIRECTION_PREFIXES = (">", "<", "1>", "2>", ">>")

VIDEO_OUTPUT_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".ts",
    ".m2ts",
    ".m4v",
    ".flv",
    ".mpeg",
    ".mpg",
    ".wmv",
    ".3gp",
}

NON_VIDEO_OUTPUT_EXTENSIONS = {
    ".gif",
    ".webp",
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".mp3",
    ".wav",
    ".aac",
    ".flac",
    ".ogg",
    ".m4a",
}


def tokenize_command(command):
    tokens = shlex.split(command, posix=False)
    normalized = []
    for token in tokens:
        if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
            normalized.append(token[1:-1])
        else:
            normalized.append(token)
    return normalized
