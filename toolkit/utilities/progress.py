"""
Progress Reporting Utilities

Consistent progress visibility across CLI tools.
Provides simple, effective progress tracking without external dependencies.
"""

import logging
import time

logger = logging.getLogger(__name__)


class ProgressReporter:
    """Simple progress reporter for CLI tools.

    Structural utility for user feedback - does NOT involve LLM.
    Use with AmplifierSession for AI operations.

    Provides consistent progress reporting that works well in both
    interactive terminals and logged output.

    Example:
        >>> from amplifier_core import AmplifierSession
        >>> from toolkit.utilities.progress import ProgressReporter
        >>>
        >>> # Define config directly (toolkit pattern)
        >>> config = {
        ...     "session": {"orchestrator": "loop-basic"},
        ...     "providers": [{
        ...         "module": "provider-anthropic",
        ...         "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        ...         "config": {"model": "claude-sonnet-4-5"}
        ...     }]
        ... }
        >>>
        >>> async with AmplifierSession(config=config) as session:
        ...     progress = ProgressReporter(len(files), "Analyzing")
        ...     for file in files:
        ...         response = await session.execute(f"Analyze: {file.read_text()}")
        ...         save_result(file, response)
        ...         progress.update()
        ...     progress.complete()
    """

    def __init__(
        self,
        total: int,
        description: str = "Processing",
        show_items: bool = True,
        log_interval: int = 10,
    ):
        """Initialize progress reporter.

        Args:
            total: Total number of items to process
            description: Description of what's being processed
            show_items: Whether to show item names in progress
            log_interval: How often to log progress (every N items)
        """
        self.total = total
        self.current = 0
        self.description = description
        self.show_items = show_items
        self.log_interval = log_interval
        self.start_time = time.time()
        self.last_log_time = time.time()

        # Log initial status
        if self.total > 0:
            logger.info(f"{self.description}: {self.total} items to process")

    def update(self, item_name: str | None = None) -> None:
        """Update progress and log current item.

        Args:
            item_name: Optional name of current item being processed
        """
        self.current += 1

        # Calculate percentage
        if self.total > 0:
            percentage = (self.current / self.total) * 100
        else:
            percentage = 0

        # Log at intervals or when complete
        should_log = (
            self.current % self.log_interval == 0
            or self.current == self.total
            or (time.time() - self.last_log_time) > 10  # Also log every 10 seconds
        )

        if should_log:
            if self.show_items and item_name:
                logger.info(f"{self.description} [{self.current}/{self.total}] ({percentage:.1f}%): {item_name}")
            else:
                logger.info(f"{self.description} [{self.current}/{self.total}] ({percentage:.1f}%)")
            self.last_log_time = time.time()

    def complete(self) -> None:
        """Mark processing complete and log summary."""
        elapsed = time.time() - self.start_time

        # Format elapsed time
        if elapsed < 60:
            time_str = f"{elapsed:.1f}s"
        else:
            minutes = int(elapsed // 60)
            seconds = elapsed % 60
            time_str = f"{minutes}m {seconds:.1f}s"

        # Calculate rate
        if elapsed > 0 and self.current > 0:
            rate = self.current / elapsed
            rate_str = f" ({rate:.1f} items/s)"
        else:
            rate_str = ""

        logger.info(f"✓ {self.description} complete: {self.current}/{self.total} items in {time_str}{rate_str}")

    def log_summary(self, successes: int, failures: int) -> None:
        """Log a summary of successes and failures.

        Args:
            successes: Number of successful items
            failures: Number of failed items
        """
        total_processed = successes + failures

        if failures == 0:
            logger.info(f"✓ All {successes} items processed successfully")
        else:
            success_rate = (successes / total_processed) * 100 if total_processed > 0 else 0
            logger.warning(
                f"Completed with {failures} failures: {successes}/{total_processed} succeeded ({success_rate:.1f}%)"
            )

    def estimate_remaining(self) -> str | None:
        """Estimate remaining time based on current rate.

        Returns:
            Formatted string with time estimate, or None if not calculable
        """
        if self.current == 0 or self.current >= self.total:
            return None

        elapsed = time.time() - self.start_time
        rate = self.current / elapsed
        remaining_items = self.total - self.current
        remaining_seconds = remaining_items / rate

        if remaining_seconds < 60:
            return f"{remaining_seconds:.0f}s"
        if remaining_seconds < 3600:
            minutes = int(remaining_seconds // 60)
            seconds = int(remaining_seconds % 60)
            return f"{minutes}m {seconds}s"
        hours = int(remaining_seconds // 3600)
        minutes = int((remaining_seconds % 3600) // 60)
        return f"{hours}h {minutes}m"


class SimpleSpinner:
    """Simple activity indicator for operations without known total.

    Structural utility for unknown-length operations - does NOT involve LLM.
    Use when you don't know how many items will be processed.

    Useful for operations where you don't know how many items
    there will be (e.g., streaming results).

    Example:
        >>> spinner = SimpleSpinner("Searching")
        >>> for result in search():
        >>>     process(result)
        >>>     spinner.spin()
        >>> spinner.stop("Found 42 results")
    """

    def __init__(self, description: str = "Working"):
        """Initialize spinner.

        Args:
            description: What's being done
        """
        self.description = description
        self.counter = 0
        self.start_time = time.time()
        self.last_update = 0
        logger.info(f"{description}...")

    def spin(self, update_interval: float = 5.0) -> None:
        """Update spinner (log activity periodically).

        Args:
            update_interval: Seconds between activity logs
        """
        self.counter += 1
        current_time = time.time()

        if current_time - self.last_update > update_interval:
            elapsed = current_time - self.start_time
            logger.info(f"  ...still {self.description.lower()} ({self.counter} items, {elapsed:.1f}s)")
            self.last_update = current_time

    def stop(self, message: str | None = None) -> None:
        """Stop spinner and log final message.

        Args:
            message: Optional completion message
        """
        elapsed = time.time() - self.start_time

        if message:
            logger.info(f"✓ {message} ({elapsed:.1f}s)")
        else:
            logger.info(f"✓ {self.description} complete ({self.counter} items, {elapsed:.1f}s)")


def log_stage(stage: str, description: str | None = None) -> None:
    """Log the start of a processing stage.

    Creates clear visual separation in logs for different stages.

    Args:
        stage: Stage name (e.g., "Validation", "Processing")
        description: Optional description of what this stage does
    """
    separator = "=" * 60
    logger.info(separator)
    logger.info(f"Stage: {stage}")
    if description:
        logger.info(f"  {description}")
    logger.info(separator)
