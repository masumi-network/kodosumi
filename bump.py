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
    
    suggested_version = f"{major_ver}.{minor_ver}.{patch_ver}"
    
    print(f"\nSuggested new version: {suggested_version}")
    print("Press Enter to accept or type a custom version (e.g., 1.2.3):")
    custom_version = input().strip()
    
    if not custom_version:
        return suggested_version
    
    # Validate custom version format
    if not re.match(r'^\d+\.\d+\.\d+$', custom_version):
        print("Error: Invalid version format. Please use format X.Y.Z")
        sys.exit(1)
    
    return custom_version

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

def update_requirements():
    """Updates requirements.txt with current dependencies using pip freeze."""
    try:
        print("\nUpdating requirements.txt...")
        result = subprocess.run(
            ["pip", "freeze"],
            capture_output=True,
            text=True,
            check=True
        )
        with open("requirements.txt", "w") as f:
            f.write(result.stdout)
        print("requirements.txt has been updated successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Error updating requirements.txt: {e}")
        sys.exit(1)

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

def ensure_main_branch():
    """Verifies we're on the main branch before making changes."""
    try:
        # Get current branch
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True
        )
        current_branch = result.stdout.strip()
        
        if current_branch != "main":
            print(f"Error: You must be on the main branch to update version.")
            print(f"Current branch: {current_branch}")
            sys.exit(1)
            
    except subprocess.CalledProcessError as e:
        print(f"Error checking branch: {e}")
        sys.exit(1)

def build_and_upload():
    """Builds the package and uploads it to PyPI."""
    try:
        print("\nBuilding package...")
        subprocess.run(["python", "-m", "build"], check=True)
        
        print("\nUploading to PyPI...")
        subprocess.run(["python", "-m", "twine", "upload", "dist/*"], check=True)
        print("Package successfully uploaded to PyPI!")
    except subprocess.CalledProcessError as e:
        print(f"Error during build or upload: {e}")
        sys.exit(1)

def main():
    # Ensure we're on main branch before proceeding
    ensure_main_branch()
    
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
    # update_requirements()
    git_commit_and_push(new_version)
    create_git_tag(new_version)
    
    # Build and upload to PyPI
    confirm = input("\nDo you want to upload to PyPI? (y/n): ").strip().lower()
    if confirm == "y":
        build_and_upload()
    
    print("\nVersion has been successfully updated and package uploaded!")

if __name__ == "__main__":
    main() 