from __future__ import annotations
import logging
from threading import Thread
from queue import Queue
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from bot.commands import CommandProcessor


class Task:
    def __init__(
        self, command_id: int, function: Callable[..., None], args: Any, kwargs: Any
    ) -> None:
        self.command_id = command_id
        self.function = function
        self.args = args
        self.kwargs = kwargs


class TaskProcessor(Thread):
    def __init__(self, command_processor: CommandProcessor) -> None:
        super().__init__(daemon=True)
        self.name = command_processor.bot.name + "/Tasks"
        self.command_processor = command_processor
        self.task_queue: Queue[Task] = Queue()

    def close(self) -> None:
        self.task_queue.put(None)

    def run(self) -> None:
        while True:
            task = self.task_queue.get()
            if task is None:
                return
            if task.command_id == self.command_processor.current_command_id:
                # An exception here would end this thread, and with it every
                # later background task on this server.
                try:
                    task.function(*task.args, **task.kwargs)
                except Exception:
                    logging.error("Background task failed", exc_info=True)
