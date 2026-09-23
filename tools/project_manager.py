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
from typing import Any, Dict, List, Optional

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_BASE_DIR = os.path.join(WORKSPACE_ROOT, "output")
ACTIVE_PROJECT_FILE = os.path.join(OUTPUT_BASE_DIR, ".active_project.json")

DEFAULT_PROJECT_NAME = "default_project"


def sanitize_project_name(name: str) -> str:
    """Sanitize arbitrary string into safe, standard folder name."""
    if not name or not name.strip():
        return DEFAULT_PROJECT_NAME
    # Replace Chinese or special punctuation with underscores or clean ASCII
    clean = name.strip()
    clean = re.sub(r'[\/\\:\*\?"<>\|\s]+', '_', clean)
    clean = re.sub(r'_+', '_', clean).strip('_')
    return clean.lower() if clean else DEFAULT_PROJECT_NAME


def get_output_base_dir() -> str:
    """Return the global output root directory."""
    os.makedirs(OUTPUT_BASE_DIR, exist_ok=True)
    return OUTPUT_BASE_DIR


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
    
    # Create subdirectories
    subdirs = {
        "cad": os.path.join(p_dir, "cad"),
        "drawings": os.path.join(p_dir, "drawings"),
        "optomech": os.path.join(p_dir, "optomech"),
        "reports": os.path.join(p_dir, "reports"),
    }
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
        "subdirectories": {
            "root": p_dir,
            "cad": subdirs["cad"],
            "drawings": subdirs["drawings"],
            "optomech": subdirs["optomech"],
            "reports": subdirs["reports"],
        },
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

    with open(proj_meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    # Persist as active project
    with open(ACTIVE_PROJECT_FILE, "w", encoding="utf-8") as f:
        json.dump({"active_project": p_clean, "last_switched": now_str}, f, indent=2)

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
    p_dir = os.path.join(get_output_base_dir(), p_name)
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


def get_project_dir(project_name: Optional[str] = None, subfolder: Optional[str] = None) -> str:
    """
    Get the absolute path to a project directory or subfolder.
    If project_name is None, uses active project.
    Ensures the returned folder exists on disk.
    """
    p_name = sanitize_project_name(project_name) if project_name else get_active_project_name()
    p_dir = os.path.join(get_output_base_dir(), p_name)
    if subfolder:
        target_dir = os.path.join(p_dir, subfolder)
    else:
        target_dir = p_dir
    os.makedirs(target_dir, exist_ok=True)
    return target_dir


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
    p_dir = get_project_dir(project_name, subfolder)
    if not user_path:
        return os.path.join(p_dir, default_filename)
    
    if os.path.isabs(user_path):
        os.makedirs(os.path.dirname(user_path), exist_ok=True)
        return user_path
    
    # Relative path
    target = os.path.join(p_dir, user_path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    return target


def list_projects() -> List[Dict[str, Any]]:
    """List all projects currently stored under output/."""
    base = get_output_base_dir()
    projects = []
    active = get_active_project_name()

    for item in os.listdir(base):
        full_p = os.path.join(base, item)
        if os.path.isdir(full_p) and not item.startswith("."):
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
