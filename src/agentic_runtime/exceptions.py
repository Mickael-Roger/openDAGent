from __future__ import annotations


class TaskBlocked(Exception):
    """Raised by spawn_subtask to signal the parent task must pause.

    The capability loop catches this to save conversation state, and the
    worker catches it to set the task state to 'blocked'.
    """

    def __init__(self, child_task_id: str) -> None:
        self.child_task_id = child_task_id
        super().__init__(f"Task blocked waiting for subtask {child_task_id}")
