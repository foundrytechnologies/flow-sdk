"""
This module provides a SpinnerLogger class that integrates a rich spinner and progress
display with Python logging. The spinner is rendered using a deep royal blue style.
It supports ephemeral sub–steps, buffering of external log messages, and a progress bar
for multi-step tasks.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Generator, List, Optional

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.status import Status


class SpinnerLogHandler(logging.Handler):
    """Custom logging handler that directs log messages to a SpinnerLogger.

    This allows external log records to be intercepted and either buffered
    or rendered as sub–steps in an active spinner.
    """

    def __init__(self, spinner_logger: SpinnerLogger, level: int = logging.INFO) -> None:
        """Initializes the log handler.

        Args:
            spinner_logger: A SpinnerLogger instance to which log messages are sent.
            level: The logging level (default: logging.INFO).
        """
        super().__init__(level=level)
        self.spinner_logger = spinner_logger

    def emit(self, record: logging.LogRecord) -> None:
        """Formats and passes the log record to the spinner logger.

        Args:
            record: The LogRecord to process.
        """
        msg: str = self.format(record)
        self.spinner_logger.handle_external_log(msg, level=record.levelno)


class SpinnerLogger:
    """Provides fancy logging with an integrated spinner and progress display.

    This class manages a spinner (using Rich's Status) that shows progress messages,
    sub–steps, and integrates external log messages. It is designed to be modular,
    extensible, and easy to integrate into a production codebase.
    """

    def __init__(self, logger: logging.Logger, spinner_delay: float = 0.1) -> None:
        """Initializes the SpinnerLogger.

        Args:
            logger: A logger instance for standard logging.
            spinner_delay: Delay between spinner updates (in seconds).
        """
        self.logger: logging.Logger = logger
        self.spinner_delay: float = spinner_delay

        # Spinner state management
        self._spinner_active: bool = False
        self._console: Optional[Console] = None
        self._status: Optional[Status] = None

        # Buffer for external logs when spinner is inactive.
        self._log_buffer: List[str] = []

        # Ephemeral sub–steps
        self._sub_steps_enabled: bool = False
        self._sub_steps: List[str] = []

    def create_log_handler(self, level: int = logging.INFO) -> SpinnerLogHandler:
        """Creates and returns a custom log handler for redirecting logs.

        Args:
            level: The logging level for the handler.

        Returns:
            A SpinnerLogHandler instance.
        """
        return SpinnerLogHandler(self, level=level)

    def handle_external_log(self, message: str, level: int = logging.INFO) -> None:
        """Handles external log messages.

        If the spinner is active, the message is added as a sub–step.
        Otherwise, it is buffered until the spinner starts.

        Args:
            message: The external log message.
            level: The logging level.
        """
        if self._spinner_active:
            self.update_sub_step(message)
        else:
            self._log_buffer.append(message)

        # Also log to the standard logger.
        if level >= logging.ERROR:
            self.logger.error(message)
        elif level >= logging.WARNING:
            self.logger.warning(message)
        elif level >= logging.DEBUG:
            self.logger.debug(message)
        else:
            self.logger.info(message)

    @contextlib.contextmanager
    def spinner(
        self,
        message: str,
        enable_sub_steps: bool = False,
        persist_messages: bool = True,
    ) -> Generator[None, None, None]:
        """Context manager to display a spinner until exiting the context.

        Args:
            message: The message to display alongside the spinner.
            enable_sub_steps: If True, ephemeral logs become sub–steps.
            persist_messages: If True, sub–steps are logged again after completion.

        Yields:
            Control back to the caller during spinner activity.
        """
        if self._spinner_active:
            # Spinner is already active; update the displayed text.
            self.logger.debug("Spinner already active. Updating message to: %s", message)
            self.update_text(message)
            yield
        else:
            self._spinner_active = True
            self._sub_steps_enabled = enable_sub_steps
            self._sub_steps.clear()
            self._console = Console()
            # Use Rich's Status widget to render the spinner with deep royal blue style.
            with self._console.status(
                message, spinner="dots", spinner_style="#002366"
            ) as status:
                self._status = status
                self.logger.info("[SPINNER START] %s", message)
                # Flush any buffered logs into the spinner as sub–steps.
                self._flush_buffer_to_spinner()
                try:
                    yield
                finally:
                    if persist_messages and self._sub_steps_enabled and self._sub_steps:
                        self.logger.info("--- Sub–steps for '%s' ---", message)
                        for step in self._sub_steps:
                            self.logger.info(" - %s", step)
                    self.logger.info("[SPINNER END] %s", message)
                    self._spinner_active = False
                    self._sub_steps_enabled = False
                    self._status = None
                    self._console = None

    def _flush_buffer_to_spinner(self) -> None:
        """Flushes buffered log messages into the spinner sub–steps."""
        for msg in self._log_buffer:
            self.update_sub_step(msg)
        self._log_buffer.clear()

    def update_text(self, message: str) -> None:
        """Updates the spinner text if active.

        Args:
            message: The new spinner message.
        """
        if self._spinner_active and self._status is not None:
            self._status.update(message)
        self.logger.info("[SPINNER] %s", message)

    def update_sub_step(self, message: str) -> None:
        """Adds an ephemeral sub–step message.

        If the spinner is active and sub–steps are enabled, the message is
        both stored and immediately rendered.

        Args:
            message: The sub–step message.
        """
        if self._sub_steps_enabled:
            self._sub_steps.append(message)
        if self._spinner_active and self._console is not None:
            self._console.log(f"[sub–step] {message}")
        else:
            self.logger.info("[sub–step] %s", message)

    def progress_bar(self, message: str, total: int) -> None:
        """Displays a progress bar for multi–step tasks.

        The progress bar uses a deep royal blue spinner for consistency.

        Args:
            message: A description of the progress task.
            total: The total number of steps.
        """
        console = Console()
        with Progress(
            SpinnerColumn(spinner_name="dots", style="#002366"),
            BarColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task_id = progress.add_task(message, total=total)
            for i in range(total):
                time.sleep(self.spinner_delay)  # Simulate work
                progress.update(task_id, advance=1)
                self.logger.info("[PROGRESS] Step %d/%d", i + 1, total)
            self.logger.info("[PROGRESS END] %s", message)

    def notify(self, message: str) -> None:
        """Logs a simple text notification.

        Args:
            message: The notification message.
        """
        self.logger.info("[NOTIFY] %s", message)
