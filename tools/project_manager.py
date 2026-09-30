"""
Zemax Project & Output Workspace Manager
Manages dedicated, isolated project folders under 'output/<project_name>/'.
Standard directory layout:
  output/<project_name>/
    ├── <project_name>.zmx / <project_name>.zos  (Active optical model)
    ├── cad/                                     (3D CAD solid models: STEP, IGES, STL)
    ├── drawings/                                (ISO 10110 specs & 2D cross-section plots)
    ├── optomech/                                (SolidWorks MCP linkage JSON)
    ├── reports/                                 (Proposal, MTF curves, Spot diagrams, Ray fans)
    └── project.json                             (Project metadata and configuration)
"""

import datetime
import json
import os
import re
import tempfile
import ntpath
from core.zos_session import synchronized
from typing import Any, Dict, List, Optional

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_BASE_DIR = os.path.join(WORKSPACE_ROOT, "output")
ACTIVE_PROJECT_FILE = os.path.join(OUTPUT_BASE_DIR, ".active_project.json")

DEFAULT_PROJECT_NAME = "default_project"
_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def sanitize_project_name(name: str) -> str:
    """Sanitize arbitrary string into safe, standard folder name."""
    if not isinstance(name, str) or not name.strip():
        return DEFAULT_PROJECT_NAME
    # Replace Chinese or special punctuation with underscores or clean ASCII
    if name != name.rstrip(". "):
        raise ValueError("Project names cannot end with a dot or space.")
    clean = name.strip()
    # A project name is a single directory component.  Reject traversal and
    # platform aliases instead of turning them into an unexpected path.
    if clean in {".", ".."} or any(ord(ch) < 32 for ch in clean):
        raise ValueError("Project name cannot be '.'/'..' or contain control characters.")
    clean = re.sub(r'[\/\\:\*\?"<>\|\s]+', '_', clean)
    clean = re.sub(r'_+', '_', clean).strip('_')
    clean = clean.rstrip(". ").lower()
    if not clean:
        raise ValueError("Project name resolves to an empty directory name.")
    if clean.split(".", 1)[0] in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"Project name '{name}' is reserved by Windows.")
    return clean


def _canonical(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _validate_path(path: str) -> bool:
    """Return whether a path is fully absolute, rejecting Windows aliases."""
    if not isinstance(path, str) or not path or path.startswith(("\\\\?\\", "\\\\.\\")):
        raise ValueError("Invalid output path.")
    drive, tail = ntpath.splitdrive(path)
    absolute = bool(drive and tail.startswith(("\\", "/")))
    if (drive or path.startswith(("\\", "/"))) and not absolute:
        raise ValueError("Use a fully qualified absolute path or a project-relative path.")
    parts = re.split(r"[\\/]", tail.lstrip("\\/") if absolute else path)
    parts = [part for part in parts if part]
    if not parts:
        if absolute:
            return True
        raise ValueError("Invalid output path.")
    for part in parts:
        if (part in (".", "..") or part.rstrip(". ") != part
                or any(ord(c) < 32 or c in ':*?"<>|' for c in part)
                or part.split(".", 1)[0].lower() in _WINDOWS_RESERVED_NAMES):
            raise ValueError("Output path contains unsafe components.")
    return absolute


def _within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([_canonical(path), _canonical(root)]) == _canonical(root)
    except ValueError:
        return False


def _atomic_json(path: str, payload: Dict[str, Any]) -> None:
    _atomic_text(path, json.dumps(payload, indent=2, ensure_ascii=False))


def _atomic_text(path: str, content: str) -> None:
    """Replace a text artifact without following an existing symlink."""
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".txt", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def get_output_base_dir() -> str:
    """Return the global output root directory."""
    os.makedirs(OUTPUT_BASE_DIR, exist_ok=True)
    return OUTPUT_BASE_DIR


@synchronized
def get_active_project_name() -> str:
    """Retrieve the currently active project name from persistent file or default."""
    if os.path.exists(ACTIVE_PROJECT_FILE):
        try:
            with open(ACTIVE_PROJECT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                pname = data.get("active_project")
                if pname:
                    return sanitize_project_name(pname)
        except Exception:
            pass
    return DEFAULT_PROJECT_NAME


@synchronized
def set_active_project(
    project_name: str,
    description: Optional[str] = None,
    target_specs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Set and initialize an active project workspace.
    Automatically creates the isolated project directory and subfolders:
    cad/, drawings/, optomech/, reports/.
    """
    p_clean = sanitize_project_name(project_name)
    p_dir = os.path.join(get_output_base_dir(), p_clean)
    if not _within(p_dir, get_output_base_dir()):
        raise ValueError("Project path escapes the output workspace.")
    
    # Create subdirectories
    subdirs = {name: os.path.join(p_dir, name) for name in ("cad", "drawings", "optomech", "reports")}
    for s_path in subdirs.values():
        os.makedirs(s_path, exist_ok=True)

    # Project metadata
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    proj_meta_path = os.path.join(p_dir, "project.json")
    meta: Dict[str, Any] = {
        "project_name": p_clean,
        "original_title": project_name,
        "created_at": now_str,
        "last_active": now_str,
        "description": description or f"Optical Design Project: {project_name}",
        "subdirectories": {"root": p_dir, **subdirs},
    }
    if target_specs:
        meta["target_specs"] = target_specs

    # If project.json already exists, update without overwriting created_at
    if os.path.exists(proj_meta_path):
        try:
            with open(proj_meta_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
                meta["created_at"] = existing.get("created_at", now_str)
                if not description and existing.get("description"):
                    meta["description"] = existing.get("description")
                if not target_specs and existing.get("target_specs"):
                    meta["target_specs"] = existing.get("target_specs")
        except Exception:
            pass

    _atomic_json(proj_meta_path, meta)

    # Persist as active project
    _atomic_json(ACTIVE_PROJECT_FILE, {"active_project": p_clean, "last_switched": now_str})

    return {
        "status": "success",
        "message": f"Active project set to '{p_clean}'. Project workspace ready.",
        "project_name": p_clean,
        "project_directory": p_dir,
        "subdirectories": subdirs,
    }


def get_active_project_info() -> Dict[str, Any]:
    """Get metadata and directory paths of the current active project."""
    p_name = get_active_project_name()
    p_dir = get_project_dir(p_name)
    proj_meta_path = os.path.join(p_dir, "project.json")
    
    if os.path.exists(proj_meta_path):
        try:
            with open(proj_meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
                return {
                    "status": "success",
                    "active_project": p_name,
                    "metadata": meta,
                }
        except Exception:
            pass

    return {
        "status": "success",
        "active_project": p_name,
        "project_directory": p_dir,
        "subdirectories": {
            "root": p_dir,
            "cad": os.path.join(p_dir, "cad"),
            "drawings": os.path.join(p_dir, "drawings"),
            "optomech": os.path.join(p_dir, "optomech"),
            "reports": os.path.join(p_dir, "reports"),
        },
    }


@synchronized
def get_project_dir(project_name: Optional[str] = None, subfolder: Optional[str] = None) -> str:
    """
    Get the absolute path to a project directory or subfolder.
    If project_name is None, uses active project.
    Ensures the returned folder exists on disk.
    """
    p_name = sanitize_project_name(project_name) if project_name else get_active_project_name()
    root = get_output_base_dir()
    p_dir = os.path.join(root, p_name)
    if not _within(p_dir, root):
        raise ValueError("Project path escapes the output workspace.")
    if subfolder:
        if _validate_path(subfolder):
            raise ValueError("Project subfolder must be a relative path.")
        target_dir = os.path.join(p_dir, subfolder)
        if not _within(target_dir, p_dir):
            raise ValueError("Project subfolder escapes the project workspace.")
    else:
        target_dir = p_dir
    os.makedirs(target_dir, exist_ok=True)
    return target_dir


@synchronized
def resolve_project_file_path(
    user_path: Optional[str],
    default_filename: str,
    subfolder: Optional[str] = None,
    project_name: Optional[str] = None,
) -> str:
    """
    Resolve a destination file path within the project workspace.
    - If user_path is an absolute path: uses it directly.
    - If user_path is a simple filename or relative path: resolves inside project directory (or subfolder).
    - If user_path is None: uses default_filename inside project directory (or subfolder).
    """
    candidate = user_path or default_filename
    if _validate_path(candidate):
        os.makedirs(os.path.dirname(candidate), exist_ok=True)
        return candidate
    target = _project_relative_target(candidate, subfolder, project_name)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    return target


def _project_relative_target(candidate: str, subfolder: Optional[str], project_name: Optional[str]) -> str:
    """Resolve a validated relative path inside the project (or subfolder)."""
    p_name = sanitize_project_name(project_name) if project_name else get_active_project_name()
    p_dir = get_project_dir(project_name, subfolder)
    # A leading "output/", "<project>/", or "<subfolder>/" is dropped so that
    # "output/<project>/cad/foo.step" lands cleanly as "<project>/cad/foo.step".
    parts = os.path.normpath(candidate).split(os.sep)
    for prefix in (os.path.basename(get_output_base_dir()), p_name, subfolder):
        if prefix and len(parts) > 1 and os.path.normcase(parts[0]) == os.path.normcase(prefix):
            parts = parts[1:]
    target = os.path.join(p_dir, *parts)
    if not _within(target, p_dir):
        raise ValueError("Relative output path escapes the project workspace.")
    return target


@synchronized
def resolve_project_directory_path(
    user_path: Optional[str],
    subfolder: Optional[str] = "drawings",
    project_name: Optional[str] = None,
) -> str:
    """Resolve an export directory while preserving absolute-path compatibility."""
    if not user_path:
        return get_project_dir(project_name, subfolder)
    target = os.path.abspath(user_path) if _validate_path(user_path) else _project_relative_target(user_path, subfolder, project_name)
    os.makedirs(target, exist_ok=True)
    return target


@synchronized
def list_projects() -> List[Dict[str, Any]]:
    """List all projects currently stored under output/."""
    base = get_output_base_dir()
    projects = []
    active = get_active_project_name()

    for item in os.listdir(base):
        full_p = os.path.join(base, item)
        if os.path.isdir(full_p) and not item.startswith(".") and _within(full_p, base):
            if item == "drawings" or item.startswith("drawings_"):
                continue
            meta_path = os.path.join(full_p, "project.json")
            meta = {}
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except Exception:
                    pass

            # Count files
            files_count = 0
            for root, _, files in os.walk(full_p):
                files_count += len(files)

            projects.append({
                "project_name": item,
                "is_active": (item == active),
                "directory": full_p,
                "total_files": files_count,
                "description": meta.get("description", ""),
                "created_at": meta.get("created_at", ""),
                "last_active": meta.get("last_active", ""),
            })

    return projects
