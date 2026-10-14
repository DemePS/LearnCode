"""Delete the repository the instructor cloned for this turn.

seedagent's clone_repo leaves its copy on disk for good (network.py:186 even suggests gitignoring it),
and its delete_folder cannot remove one: a clone contains a .git folder and files.py:362 refuses any
folder that does -- "is or contains a git repository (.git); it cannot be deleted by the agent". So
the copy would accumulate, one per learner per turn, and nothing in seedagent will tidy it up.

This is the one tool that removes it. It is deliberately narrow:

  * the path must be inside the session workspace (resolve() refuses anything outside it);
  * the folder must look like a clone -- a .git entry at its top level. A folder that is not a clone
    is refused, so this cannot become a general "delete any folder" tool;
  * it never touches the workspace root itself.

The agent is expected to call it once it has read what it needs, and CaptureUI approves the network
panel and nothing else, so this tool asks no one.
"""

import os
import shutil
import stat
from pathlib import Path

from seedagent.common import ToolError, resolve, rel_name, display


def tool_delete_clone(path: str) -> str:
    """Delete a cloned repository folder (must contain a .git entry) from the session workspace."""
    if not isinstance(path, str) or not path.strip():
        raise ToolError("Give the folder of the clone to delete, for example 'fastapi'.")

    target = resolve(path.strip())
    if target == Path(target.anchor):
        raise ToolError("Refusing to delete a filesystem root.")
    if not target.exists():
        raise ToolError(f"No such folder: {path}")
    if not target.is_dir():
        raise ToolError(f"{path} is a file, not the folder of a clone; use delete_file for it.")
    if not (target / ".git").exists():
        raise ToolError(
            f"{rel_name(target)} is not a cloned repository (no .git in it), so this tool will not "
            f"delete it. Only copies made by clone_repo can be removed here."
        )

    files = sum(len(names) for _, _, names in os.walk(target))
    try:
        # git marks its object files read-only on Windows; onerror clears that and retries once.
        shutil.rmtree(target, onerror=_force_remove)
    except OSError as e:
        raise ToolError(f"Deletion stopped partway: {e}")

    note = ""
    try:
        from seedagent import state
        if state.cwd == target or target in state.cwd.parents:
            state.cwd = state.workspace
            note = " The current directory was inside it and is now the workspace root."
    except Exception:  # noqa: BLE001 -- tidying the cwd is a nicety, never a reason to fail
        pass
    return f"Deleted the cloned repository {display(target)}/ ({files} files).{note}"


def _force_remove(func, path, _exc):
    """shutil.rmtree callback: clear the read-only bit git sets on its objects, then retry once."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


SCHEMA = {
    "name": "delete_clone",
    "description": (
        "Delete a repository folder you cloned with clone_repo, once you have read what you need from "
        "it. Call this before you finish the turn: the copy is otherwise left on the server's disk. "
        "It only deletes folders that are clones (they contain .git); anything else is refused. Give "
        "the path you passed as clone_repo's destination."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string",
                     "description": "The folder you cloned into, e.g. 'fastapi' or 'repos/fastapi'."},
        },
        "required": ["path"],
    },
}
