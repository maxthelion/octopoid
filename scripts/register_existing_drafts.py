#!/usr/bin/env python3
"""Register existing draft files in the database.

Scans project-management/drafts/{boxen,octopoid}/ for all .md files and
registers them in the drafts table. Safe to run multiple times - skips
already-registered files.
"""

import re
import sys
from pathlib import Path
from datetime import datetime

# Add parent directory to path to import orchestrator modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator import db


def parse_draft_metadata(content: str, file_path: Path) -> dict:
    """Parse metadata from draft markdown content.

    Args:
        content: Full markdown content
        file_path: Path to the file (used as fallback for title)

    Returns:
        Dict with title, status, author
    """
    lines = content.split('\n')

    # Extract title (first h1 heading)
    title = None
    for line in lines[:20]:  # Check first 20 lines
        if line.startswith('# '):
            title = line[2:].strip()
            break

    if not title:
        # Fallback: use filename without extension and number prefix
        title = re.sub(r'^\d+-\d{4}-\d{2}-\d{2}-', '', file_path.stem)
        title = title.replace('-', ' ').title()

    # Extract status from header (look for "**Status:**" pattern)
    status = "idea"  # Default
    for line in lines[:30]:  # Check first 30 lines for metadata
        if re.match(r'\*\*Status:\*\*\s*(.+)', line, re.IGNORECASE):
            match = re.match(r'\*\*Status:\*\*\s*(.+)', line, re.IGNORECASE)
            if match:
                status_text = match.group(1).strip().lower()
                # Map common status values
                if status_text in ['idea', 'discussion', 'proposed', 'approved', 'archived', 'rejected']:
                    status = status_text
                break

    # Extract author (look for "**Author:**" pattern)
    author = "human"  # Default
    for line in lines[:30]:
        if re.match(r'\*\*Author:\*\*\s*(.+)', line, re.IGNORECASE):
            match = re.match(r'\*\*Author:\*\*\s*(.+)', line, re.IGNORECASE)
            if match:
                author = match.group(1).strip()
                break

    # Check git log for author if still default
    if author == "human":
        try:
            import subprocess
            result = subprocess.run(
                ['git', 'log', '--follow', '--format=%an', '--', str(file_path)],
                capture_output=True,
                text=True,
                cwd=file_path.parent.parent.parent,  # repo root
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                # Use first author from git history
                git_author = result.stdout.strip().split('\n')[-1]  # Oldest commit
                if git_author and git_author != "maxwilliams":
                    author = git_author
        except Exception:
            pass  # Fallback to default

    return {
        'title': title,
        'status': status,
        'author': author,
    }


def get_file_created_time(file_path: Path) -> str:
    """Get file creation time as ISO string.

    Args:
        file_path: Path to file

    Returns:
        ISO formatted timestamp
    """
    try:
        # Try to get git creation time (first commit)
        import subprocess
        result = subprocess.run(
            ['git', 'log', '--follow', '--format=%aI', '--reverse', '--', str(file_path)],
            capture_output=True,
            text=True,
            cwd=file_path.parent.parent.parent,  # repo root
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split('\n')[0]
    except Exception:
        pass

    # Fallback: use file modification time
    return datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()


def generate_draft_id(file_path: Path) -> str:
    """Generate a draft ID from file path.

    Args:
        file_path: Path to draft file

    Returns:
        Draft ID (DRAFT-<stem>)
    """
    # Remove date prefix if present (XXX-YYYY-MM-DD-)
    stem = re.sub(r'^\d+-\d{4}-\d{2}-\d{2}-', '', file_path.stem)
    return f"DRAFT-{stem}"


def register_draft_file(file_path: Path, repo_root: Path, dry_run: bool = False) -> bool:
    """Register a single draft file in the database.

    Args:
        file_path: Path to draft file
        repo_root: Path to repository root
        dry_run: If True, only print what would be done

    Returns:
        True if draft was registered, False if skipped
    """
    # Generate ID and check if already exists
    draft_id = generate_draft_id(file_path)
    existing = db.get_draft(draft_id)
    if existing:
        return False  # Already registered

    # Read file content
    try:
        content = file_path.read_text()
    except Exception as e:
        print(f"ERROR: Could not read {file_path}: {e}")
        return False

    # Parse metadata
    metadata = parse_draft_metadata(content, file_path)

    # Determine domain from path
    domain = None
    if '/drafts/boxen/' in str(file_path):
        domain = 'boxen'
    elif '/drafts/octopoid/' in str(file_path):
        domain = 'octopoid'

    # Get file creation time
    created_at = get_file_created_time(file_path)

    # Store relative path from repo root
    rel_path = file_path.relative_to(repo_root)

    if dry_run:
        print(f"Would register: {draft_id}")
        print(f"  Title: {metadata['title']}")
        print(f"  Status: {metadata['status']}")
        print(f"  Author: {metadata['author']}")
        print(f"  Domain: {domain}")
        print(f"  Path: {rel_path}")
        print(f"  Created: {created_at}")
        return True

    # Create draft in database
    db.create_draft(
        draft_id=draft_id,
        title=metadata['title'],
        author=metadata['author'],
        file_path=str(rel_path),
        status=metadata['status'],
        domain=domain,
        tags=None,
        linked_task_id=None,
        linked_project_id=None,
    )

    print(f"Registered: {draft_id} - {metadata['title']}")
    return True


def main():
    """Scan and register all draft files."""
    import argparse
    parser = argparse.ArgumentParser(description='Register existing draft files in database')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without making changes')
    args = parser.parse_args()

    # Find repo root (parent of .orchestrator directory)
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent.parent  # scripts/ -> orchestrator/ -> repo/

    # Scan draft directories
    drafts_dir = repo_root / 'project-management' / 'drafts'
    if not drafts_dir.exists():
        print(f"ERROR: Drafts directory not found: {drafts_dir}")
        sys.exit(1)

    # Find all markdown files in boxen/ and octopoid/ subdirectories
    draft_files = []
    for subdir in ['boxen', 'octopoid']:
        subdir_path = drafts_dir / subdir
        if subdir_path.exists():
            draft_files.extend(subdir_path.glob('*.md'))

    if not draft_files:
        print("No draft files found")
        return

    print(f"Found {len(draft_files)} draft files")
    if args.dry_run:
        print("DRY RUN - no changes will be made\n")

    # Register each file
    registered = 0
    skipped = 0

    for file_path in sorted(draft_files):
        if register_draft_file(file_path, repo_root, dry_run=args.dry_run):
            registered += 1
        else:
            skipped += 1

    print(f"\nRegistered: {registered}")
    print(f"Skipped (already registered): {skipped}")


if __name__ == '__main__':
    main()
