import datetime
import logging
from typing import Any, Callable, List, Optional

from rich.table import Table

from .base_formatter import Formatter
from ..models.instance import Instance
from flow.models import Bid

# TODO (jaredquincy): Consider making this configurable.
DEFAULT_MAX_ROWS = 5

# A constant for missing values.
MISSING_VALUE = "N/A"


class TableFormatter(Formatter):
    """
    Formatter for displaying status information in a table format.

    Attributes:
        max_rows (int): The maximum number of items (bids or instances) to display.
    """

    def __init__(self, max_rows: int = DEFAULT_MAX_ROWS) -> None:
        """
        Initialize the TableFormatter with a specified maximum number of rows.

        Args:
            max_rows: The maximum number of rows to display for bids/instances.
        """
        super().__init__()
        self.max_rows = max_rows
        self.logger = logging.getLogger(__name__)
        self.logger.debug("TableFormatter initialized with max_rows=%d", max_rows)

    def format_status(self, bids: List[Bid], instances: List[Instance]) -> None:
        """
        Format and print bid and instance information to the console.

        Args:
            bids: A list of Bid objects.
            instances: A list of Instance objects.
        """
        self.logger.info(
            "Formatting status for %d bids and %d instances.", len(bids), len(instances)
        )
        self.format_bids(bids)
        self.format_instances(instances)

    def _safe_format(
        self,
        value: Optional[Any],
        default: str = MISSING_VALUE,
        formatter: Optional[Callable[[Any], str]] = None,
    ) -> str:
        """
        Helper to safely format a value.

        Args:
            value: The value to format.
            default: The default string if the value is None or empty.
            formatter: A callable to format the value if provided.

        Returns:
            A formatted string.
        """
        if value is None or value == "":
            return default
        if formatter:
            try:
                return formatter(value)
            except Exception as e:
                self.logger.exception("Error formatting value %r: %s", value, e)
                return default
        return str(value)

    def format_bids(self, bids: List[Bid]) -> None:
        """
        Format and print a table of bids to the console.

        Args:
            bids: A list of Bid objects.
        """
        if not bids:
            self.logger.info("No bids found to display.")
            self.console.print("\n\nNo bids found.", style="bold yellow")
            return

        table = Table(
            title="Current Bids",
            title_style="bold",
            header_style="bold",
            border_style="dim",
        )
        table.add_column("Name", style="cyan")
        table.add_column("Type", style="green")
        table.add_column("Quantity", style="blue")
        table.add_column("Created", style="magenta")
        # TODO: Enable region when available.
        # table.add_column("Region", style="yellow")
        table.add_column("Status", style="red")

        # Limit to the maximum number of rows
        for bid in bids[: self.max_rows]:
            quantity_str = self._safe_format(bid.instance_quantity)
            # If bid.created_at is a datetime, format it. Otherwise, just use safe_format.
            created_str = (
                self._safe_format(
                    bid.created_at, formatter=lambda d: d.strftime("%Y-%m-%d %H:%M:%S")
                )
                if isinstance(bid.created_at, datetime.datetime)
                else self._safe_format(bid.created_at)
            )
            # Use getattr to support the optional 'region' attribute.
            region_str = self._safe_format(getattr(bid, "region", None))
            table.add_row(
                self._safe_format(bid.name),
                self._safe_format(bid.instance_type_id),
                quantity_str,
                created_str,
                # region_str,  # Uncomment when region is supported.
                self._safe_format(bid.status),
            )

        self.logger.debug("Displaying %d bid rows.", min(len(bids), self.max_rows))
        self.console.print(table)

    def format_instances(self, instances: List[Instance]) -> None:
        """
        Format and print a table of instances to the console.

        Args:
            instances: A list of Instance objects.
        """
        if not instances:
            self.logger.info("No instances found to display.")
            self.console.print("No instances found.", style="bold yellow")
            return

        def _get_start_date(instance: Instance) -> datetime.datetime:
            """
            Retrieve the instance's start date or use a minimal datetime if missing.

            Args:
                instance: The Instance object.

            Returns:
                A datetime representing the start date or datetime.min if missing.
            """
            return instance.start_date or datetime.datetime.min

        # Sort instances by the newest start date first.
        instances_sorted = sorted(instances, key=_get_start_date, reverse=True)

        table = Table(
            title="Current Instances",
            title_style="bold",
            header_style="bold",
            border_style="dim",
        )
        table.add_column("Name", style="cyan")
        table.add_column("Type", style="green")
        table.add_column("Status", style="magenta")
        table.add_column("Created", style="blue")
        # TODO: Enable region when available.
        # table.add_column("Region", style="yellow")
        table.add_column("IP Address", style="cyan")
        table.add_column("Category", style="green")

        for instance in instances_sorted[: self.max_rows]:
            created_str = (
                self._safe_format(
                    instance.start_date,
                    default=MISSING_VALUE,
                    formatter=lambda d: d.strftime("%Y-%m-%d %H:%M:%S"),
                )
                if instance.start_date
                else MISSING_VALUE
            )
            table.add_row(
                self._safe_format(instance.name),
                self._safe_format(
                    getattr(instance, "instance_type_id", None), default="Unknown"
                ),
                self._safe_format(instance.instance_status),
                created_str,
                # self._safe_format(getattr(instance, "region", None), default="Unknown"),  # TODO: Uncomment when region is supported.
                self._safe_format(getattr(instance, "ip_address", None), default="..."),
                self._safe_format(getattr(instance, "category", None)),
            )

        self.logger.debug(
            "Displaying %d instance rows.", min(len(instances), self.max_rows)
        )
        self.console.print(table)
