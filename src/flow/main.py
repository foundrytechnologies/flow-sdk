"""
Flow CLI - Manage your Foundry tasks and instances.

This module provides a command-line interface (CLI) for submitting, checking
the status of, and canceling bids/tasks on Foundry. It leverages a FoundryClient
for communication, as well as various manager classes (AuctionFinder, BidManager,
FlowTaskManager) that encapsulate the corresponding logic.

Usage Example:
    flow submit /path/to/config.yaml --verbose
    flow status --task-name my-task --show-all
    flow cancel --task-name my-task
"""

import argparse
import logging
import sys
import traceback
from typing import Optional, List

from flow.config import get_config
from flow.clients.foundry_client import FoundryClient
from flow.task_config import ConfigParser
from flow.managers.auction_finder import AuctionFinder
from flow.managers.bid_manager import BidManager
from flow.managers.task_manager import FlowTaskManager
from flow.logging.spinner_logger import SpinnerLogger


def configure_logging(verbosity: int) -> None:
    """Configure logging level based on verbosity count.

    Args:
        verbosity (int): The verbosity level as provided by command-line arguments.
    """
    if verbosity == 1:
        logging.getLogger().setLevel(logging.INFO)
    elif verbosity >= 2:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.WARNING)


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        argparse.Namespace: Parsed arguments containing the command, config_file, verbosity, etc.
    """
    parser = argparse.ArgumentParser(
        prog="flow", description="Flow CLI - Manage your Foundry tasks and instances."
    )
    parser.add_argument(
        "command", choices=["submit", "status", "cancel"], help="Command to execute."
    )
    parser.add_argument(
        "config_file",
        nargs="?",
        help="Path to the configuration YAML file (required for 'submit').",
    )
    parser.add_argument(
        "--task-name",
        help="Name of the task to filter on (required for 'cancel', optional otherwise).",
    )
    parser.add_argument(
        "--format",
        choices=["table"],
        default="table",
        help="Output format (default: table).",
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Show all entries, including ones with missing data.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase output verbosity (use multiple times for more detail).",
    )
    return parser.parse_args()


def initialize_foundry_client() -> FoundryClient:
    """Initialize and return a FoundryClient based on environment or config values.

    Returns:
        FoundryClient: A configured FoundryClient instance ready for use.

    Raises:
        Exception: If initialization fails for any reason.
    """
    config = get_config()  # Possibly from environment variables, config files, etc.
    foundry_client = FoundryClient(
        email=config.foundry_email,
        password=config.foundry_password.get_secret_value(),
    )
    logging.getLogger(__name__).info("Initialized FoundryClient successfully.")
    return foundry_client


def run_submit_command(
    config_file: str,
    foundry_client: FoundryClient,
    auction_finder: AuctionFinder,
    bid_manager: BidManager,
) -> None:
    """Handle the 'submit' command workflow.

    Args:
        config_file (str): Path to the user-provided configuration file.
        foundry_client (FoundryClient): Client for Foundry interactions.
        auction_finder (AuctionFinder): Auction finder manager.
        bid_manager (BidManager): Bid manager.
    """
    logger = logging.getLogger(__name__)

    if not config_file:
        logger.error("Config file is required for the 'submit' command.")
        sys.exit(1)

    logger.debug("Parsing configuration file: %s", config_file)
    config_parser = ConfigParser(config_file)
    logger.info("Configuration parsed successfully.")

    # Initialize the task manager.
    task_manager = FlowTaskManager(
        config_parser=config_parser,
        foundry_client=foundry_client,
        auction_finder=auction_finder,
        bid_manager=bid_manager,
    )

    logger.info("Running the flow task manager.")
    task_manager.run()
    # (Optional: retrieve bids from FoundryClient if needed)
    bids = bid_manager.get_bids(
        project_id=config_parser.config.project_id
    )  # type: List[Bid]
    bid_ids = ", ".join(b.id for b in bids if b.id)
    logger.info("Bid(s) submitted successfully with bid id(s): %s", bid_ids)


def run_status_command(
    task_name: Optional[str],
    show_all: bool,
    foundry_client: FoundryClient,
    auction_finder: AuctionFinder,
    bid_manager: BidManager,
) -> None:
    """Handle the 'status' command workflow.

    Args:
        task_name (Optional[str]): The name of the task to filter on.
        show_all (bool): Whether to show entries with missing data.
        foundry_client (FoundryClient): Client for Foundry interactions.
        auction_finder (AuctionFinder): Auction finder manager.
        bid_manager (BidManager): Bid manager.
    """
    logger = logging.getLogger(__name__)
    logger.info("Checking status for tasks.")

    task_manager = FlowTaskManager(
        config_parser=None,
        foundry_client=foundry_client,
        auction_finder=auction_finder,
        bid_manager=bid_manager,
    )
    task_manager.check_status(task_name=task_name, show_all=show_all)


def run_cancel_command(
    task_name: Optional[str],
    foundry_client: FoundryClient,
    auction_finder: AuctionFinder,
    bid_manager: BidManager,
) -> None:
    """Handle the 'cancel' command workflow.

    Args:
        task_name (Optional[str]): The name of the task to cancel.
        foundry_client (FoundryClient): Client for Foundry interactions.
        auction_finder (AuctionFinder): Auction finder manager.
        bid_manager (BidManager): Bid manager.
    """
    logger = logging.getLogger(__name__)

    if not task_name:
        logger.error("Task name is required for the 'cancel' command.")
        sys.exit(1)

    logger.info("Attempting to cancel task: %s", task_name)
    task_manager = FlowTaskManager(
        config_parser=None,
        foundry_client=foundry_client,
        auction_finder=auction_finder,
        bid_manager=bid_manager,
    )
    task_manager.cancel_bid(name=task_name)
    logger.info("Task '%s' has been canceled successfully.", task_name)


def main() -> int:
    """Main entry point for the Flow CLI.

    Returns:
        int: Exit code indicating success (0) or failure (non-zero).
    """
    exit_code = 0

    try:
        args = parse_arguments()
        configure_logging(args.verbose)
        logger = logging.getLogger(__name__)
        # Create the spinner logger and a handler for sub-steps
        spinner_logger = SpinnerLogger(
            logger=logger
        )  # Attach to the main logger or any logger

        config_parser_logger = logging.getLogger("flow.task_config")
        config_parser_handler = spinner_logger.create_log_handler(level=logging.INFO)
        config_parser_logger.addHandler(config_parser_handler)

        with spinner_logger.spinner(
            "Initializing foundry client...", enable_sub_steps=True
        ):
            foundry_client = (
                initialize_foundry_client()
            )  # logs will appear as sub-steps
            # Initialize managers
            auction_finder = AuctionFinder(foundry_client=foundry_client)
            bid_manager = BidManager(foundry_client=foundry_client)
        # Command dispatch
        if args.command == "submit":
            with spinner_logger.spinner("", enable_sub_steps=True):
                # Inside this spinner context, logs from 'flow.task_config'
                # or the same logger we attached will appear as sub-steps
                run_submit_command(
                    config_file=args.config_file,
                    foundry_client=foundry_client,
                    auction_finder=auction_finder,
                    bid_manager=bid_manager,
                )
        elif args.command == "status":
            with spinner_logger.spinner("Checking status...", enable_sub_steps=True):
                run_status_command(
                    task_name=args.task_name,
                    show_all=args.show_all,
                    foundry_client=foundry_client,
                    auction_finder=auction_finder,
                    bid_manager=bid_manager,
                )
        elif args.command == "cancel":
            with spinner_logger.spinner("Canceling task...", enable_sub_steps=True):
                run_cancel_command(
                    task_name=args.task_name,
                    foundry_client=foundry_client,
                    auction_finder=auction_finder,
                    bid_manager=bid_manager,
                )

    except KeyboardInterrupt:
        logging.getLogger(__name__).warning("Execution interrupted by user.")
        exit_code = 130
    except Exception as ex:
        logging.getLogger(__name__).error(
            "A critical error occurred in the Flow CLI.", exc_info=True
        )
        exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
