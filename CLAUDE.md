You may:
- Run destructive commands inside this repo
- Rewrite large sections of code if it simplifies the system
- Ignore backwards compatibility unless explicitly required


Save plans to the filesystem in project-management/drafts

Never `cd` into a directory that might be deleted (e.g. worktrees, temp dirs). Use absolute paths or subshells instead. The Bash tool persists CWD between commands, so if the directory is removed, every subsequent command will silently fail.

Use the `/pause-system` and `/pause-agent` skills to pause/unpause the orchestrator. Don't manually touch the PAUSE file.