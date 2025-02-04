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
    parser.add_argument(
        "--project-name",
        help="Foundry project name (if not supplied, you will be prompted or loaded from config).",
    )
    parser.add_argument(
        "--ssh-key-name",
        help="Foundry SSH key name (if not supplied, you will be prompted or loaded from config).",
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


def resolve_project_and_ssh_key(
    cli_project_name: Optional[str],
    cli_ssh_key_name: Optional[str],
    config_parser: Optional[ConfigParser],
) -> tuple[str, str]:
    """
    Resolves the Foundry project name and SSH key name by:
      1) Checking CLI flags (--project-name, --ssh-key-name),
      2) Falling back to config file (if config_parser is provided and has those fields),
      3) Finally, prompting the user if still not set.

    Returns:
        (project_name, ssh_key_name): Both as non-empty strings.
    Raises:
        SystemExit: If the user cannot provide valid values interactively.
    """
    project_name = cli_project_name
    ssh_key_name = cli_ssh_key_name

    # 1) If we have a config_parser, we can try to read config from config file
    if config_parser is not None and config_parser.config is not None:
        config_model = config_parser.config
        # Check if the config file also has project_name or ssh_key_name
        if not project_name and getattr(config_model, "project_name", None):
            project_name = config_model.project_name
        if not ssh_key_name and getattr(config_model, "ssh_key_name", None):
            ssh_key_name = config_model.ssh_key_name

    # 2) If either is still missing, prompt the user for them
    if not project_name:
        project_name = input("Please provide your Foundry project name: ").strip()
    if not ssh_key_name:
        ssh_key_name = input("Please provide your Foundry SSH key name: ").strip()

    # 3) Validate that both exist now
    if not project_name:
        logging.getLogger(__name__).error("No valid project name was provided.")
        sys.exit(1)
    if not ssh_key_name:
        logging.getLogger(__name__).error("No valid SSH key name was provided.")
        sys.exit(1)

    return project_name, ssh_key_name


def run_submit_command(
    config_file: str,
    foundry_client: FoundryClient,
    auction_finder: AuctionFinder,
    bid_manager: BidManager,
    cli_project_name: Optional[str],
    cli_ssh_key_name: Optional[str],
) -> None:
    """
    Handle the 'submit' command workflow.
    """
    logger = logging.getLogger(__name__)

    if not config_file:
        logger.error("Config file is required for the 'submit' command.")
        sys.exit(1)

    logger.debug("Parsing configuration file: %s", config_file)
    config_parser = ConfigParser(config_file)
    logger.info("Configuration parsed successfully.")

    # Resolve final project + ssh key from CLI and config
    project_name, ssh_key_name = resolve_project_and_ssh_key(
        cli_project_name=cli_project_name,
        cli_ssh_key_name=cli_ssh_key_name,
        config_parser=config_parser,
    )

    # Initialize the task manager.
    task_manager = FlowTaskManager(
        config_parser=config_parser,
        foundry_client=foundry_client,
        auction_finder=auction_finder,
        bid_manager=bid_manager,
        project_name=project_name,
        ssh_key_name=ssh_key_name,
    )

    logger.info("Running the flow task manager.")
    task_manager.run()


def run_status_command(
    task_name: Optional[str],
    show_all: bool,
    foundry_client: FoundryClient,
    auction_finder: AuctionFinder,
    bid_manager: BidManager,
    cli_project_name: Optional[str],
    cli_ssh_key_name: Optional[str],
    config_file: Optional[str] = None,
) -> None:
    logger = logging.getLogger(__name__)
    logger.info("Checking status for tasks.")

    # If config_file is provided, parse it
    config_parser = None
    if config_file:
        config_parser = ConfigParser(config_file)

    # Merge logic
    project_name, ssh_key_name = resolve_project_and_ssh_key(
        cli_project_name=cli_project_name,
        cli_ssh_key_name=cli_ssh_key_name,
        config_parser=config_parser,
    )

    task_manager = FlowTaskManager(
        config_parser=config_parser,  # Might be None if no config_file
        foundry_client=foundry_client,
        auction_finder=auction_finder,
        bid_manager=bid_manager,
        project_name=project_name,
        ssh_key_name=ssh_key_name,
    )
    task_manager.check_status(task_name=task_name, show_all=show_all)


def run_cancel_command(
    task_name: Optional[str],
    foundry_client: FoundryClient,
    auction_finder: AuctionFinder,
    bid_manager: BidManager,
    cli_project_name: Optional[str],
    cli_ssh_key_name: Optional[str],
    config_file: Optional[str] = None,
) -> None:
    logger = logging.getLogger(__name__)

    if not task_name:
        logger.error("Task name is required for the 'cancel' command.")
        sys.exit(1)

    logger.info("Attempting to cancel task: %s", task_name)

    config_parser = None
    if config_file:
        config_parser = ConfigParser(config_file)

    # Merge logic
    project_name, ssh_key_name = resolve_project_and_ssh_key(
        cli_project_name=cli_project_name,
        cli_ssh_key_name=cli_ssh_key_name,
        config_parser=config_parser,
    )

    task_manager = FlowTaskManager(
        config_parser=config_parser,
        foundry_client=foundry_client,
        auction_finder=auction_finder,
        bid_manager=bid_manager,
        project_name=project_name,
        ssh_key_name=ssh_key_name,
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
        spinner_logger = SpinnerLogger(logger=logger)

        if not args.project_name:
            args.project_name = input(
                "Please provide your Foundry project name: "
            ).strip()
        if not args.ssh_key_name:
            args.ssh_key_name = input(
                "Please provide your Foundry SSH key name: "
            ).strip()

        with spinner_logger.spinner(
            "Initializing foundry client...", enable_sub_steps=True
        ):
            foundry_client = initialize_foundry_client()
            auction_finder = AuctionFinder(foundry_client=foundry_client)
            bid_manager = BidManager(foundry_client=foundry_client)

        if args.command == "submit":
            with spinner_logger.spinner("", enable_sub_steps=True):
                run_submit_command(
                    config_file=args.config_file,
                    foundry_client=foundry_client,
                    auction_finder=auction_finder,
                    bid_manager=bid_manager,
                    cli_project_name=args.project_name,
                    cli_ssh_key_name=args.ssh_key_name,
                )
        elif args.command == "status":
            with spinner_logger.spinner("Checking status...", enable_sub_steps=True):
                run_status_command(
                    task_name=args.task_name,
                    show_all=args.show_all,
                    foundry_client=foundry_client,
                    auction_finder=auction_finder,
                    bid_manager=bid_manager,
                    cli_project_name=args.project_name,
                    cli_ssh_key_name=args.ssh_key_name,
                    config_file=args.config_file,
                )
        elif args.command == "cancel":
            with spinner_logger.spinner("Canceling task...", enable_sub_steps=True):
                run_cancel_command(
                    task_name=args.task_name,
                    foundry_client=foundry_client,
                    auction_finder=auction_finder,
                    bid_manager=bid_manager,
                    cli_project_name=args.project_name,
                    cli_ssh_key_name=args.ssh_key_name,
                    config_file=args.config_file,
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
