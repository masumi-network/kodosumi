#!/usr/bin/env python3
import re
import subprocess
import sys
from pathlib import Path
from typing import Tuple

import toml

def get_current_version() -> Tuple[str, str]:
    """Reads the current version from pyproject.toml and core.py."""
    pyproject_path = Path("pyproject.toml")
    pyproject_data = toml.load(pyproject_path)
    pyproject_version = pyproject_data["project"]["version"]
    
    core_path = Path("kodosumi/core.py")
    with open(core_path, "r") as f:
        core_content = f.read()
        match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', core_content)
        core_version = match.group(1) if match else None
    
    if pyproject_version != core_version:
        print(f"Warning: Version numbers do not match!")
        print(f"pyproject.toml: {pyproject_version}")
        print(f"core.py: {core_version}")
        sys.exit(1)
    
    return pyproject_version, core_version

def update_version(major: bool, minor: bool, patch: bool) -> str:
    """Updates the version number based on the selected options."""
    current_version, _ = get_current_version()
    major_ver, minor_ver, patch_ver = map(int, current_version.split("."))
    
    if major:
        major_ver += 1
        minor_ver = 0
        patch_ver = 0
    elif minor:
        minor_ver += 1
        patch_ver = 0
    elif patch:
        patch_ver += 1
    
    new_version = f"{major_ver}.{minor_ver}.{patch_ver}"
    return new_version

def update_files(new_version: str):
    """Updates the version number in both files."""
    pyproject_path = Path("pyproject.toml")
    pyproject_data = toml.load(pyproject_path)
    pyproject_data["project"]["version"] = new_version
    with open(pyproject_path, "w") as f:
        toml.dump(pyproject_data, f)
    
    core_path = Path("kodosumi/core.py")
    with open(core_path, "r") as f:
        content = f.read()
    new_content = re.sub(
        r'__version__\s*=\s*["\']([^"\']+)["\']',
        f'__version__ = "{new_version}"',
        content
    )
    with open(core_path, "w") as f:
        f.write(new_content)

def git_commit_and_push(version: str):
    """Commits the version changes and pushes them to remote."""
    try:
        subprocess.run(["git", "add", "pyproject.toml", "kodosumi/core.py"], check=True)
        subprocess.run(["git", "commit", "-m", f"chore: bump version to {version}"], check=True)
        subprocess.run(["git", "push", "origin", "main"], check=True)
        print(f"Changes have been committed and pushed to remote.")
    except subprocess.CalledProcessError as e:
        print(f"Error committing and pushing changes: {e}")
        sys.exit(1)

def create_git_tag(version: str):
    """Creates a Git tag and pushes it to remote."""
    try:
        subprocess.run(["git", "tag", f"v{version}"], check=True)
        subprocess.run(["git", "push", "origin", f"v{version}"], check=True)
        print(f"Tag v{version} has been created and pushed.")
    except subprocess.CalledProcessError as e:
        print(f"Error creating Git tag: {e}")
        sys.exit(1)

def main():
    current_version, _ = get_current_version()
    print(f"Current version: {current_version}")
    
    print("\nWhich version number should be increased?")
    print("1) Major (X.0.0)")
    print("2) Minor (0.X.0)")
    print("3) Patch (0.0.X)")
    
    while True:
        choice = input("\nPlease choose (1-3): ").strip()
        if choice in ["1", "2", "3"]:
            break
        print("Please choose 1, 2, or 3.")
    
    major = choice == "1"
    minor = choice == "2"
    patch = choice == "3"
    
    new_version = update_version(major, minor, patch)
    print(f"\nNew version will be: {new_version}")
    
    confirm = input("\nDo you want to proceed? (y/n): ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)
    
    update_files(new_version)
    git_commit_and_push(new_version)
    create_git_tag(new_version)
    print("\nVersion has been successfully updated!")

if __name__ == "__main__":
    main() 