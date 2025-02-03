import datetime
import logging
from typing import Any, Callable, List, Optional

from rich.table import Table

from .base_formatter import Formatter
from ..models.instance import Instance
from flow.models import Bid


# TODO (jaredquincy): Consider making this configurable.

DEFAULT_MAX_ROWS: int = 5
MISSING_VALUE: str = "N/A"


class TableFormatter(Formatter):
    """Formatter for displaying status information in a table format.

    This class uses rich.Table to render bid and instance data in a structured,
    human‐readable table. All output is sent to a rich Console instance.

    Attributes:
        max_rows (int): The maximum number of rows to display.
    """

    def __init__(self, max_rows: int = DEFAULT_MAX_ROWS) -> None:
        """Initializes a TableFormatter instance.

        Args:
            max_rows: Maximum number of rows to display for bids and instances.
        """
        super().__init__()
        self.max_rows: int = max_rows
        self.logger: logging.Logger = logging.getLogger(__name__)
        self.logger.debug("TableFormatter initialized with max_rows=%d", max_rows)

    def format_status(self, bids: List[Bid], instances: List[Instance]) -> None:
        """Formats and prints bid and instance information to the console.

        Args:
            bids: A list of Bid objects.
            instances: A list of Instance objects.
        """
        self.logger.info(
            "Formatting status for %d bids and %d instances.",
            len(bids),
            len(instances),
        )
        self.format_bids(bids)
        self.format_instances(instances)

    def _safe_format(
        self,
        value: Optional[Any],
        default: str = MISSING_VALUE,
        formatter: Optional[Callable[[Any], str]] = None,
    ) -> str:
        """Safely formats a value using an optional custom formatter.

        Args:
            value: The value to be formatted.
            default: Fallback string if the value is None or empty.
            formatter: Optional callable that returns a string given the value.

        Returns:
            A string representation of the value.
        """
        if value is None or value == "":
            return default
        if formatter:
            try:
                return formatter(value)
            except Exception as err:
                self.logger.exception("Error formatting value %r: %s", value, err)
                return default
        return str(value)

    @staticmethod
    def _format_datetime(dt: datetime.datetime) -> str:
        """Converts a datetime object to a formatted string.

        Args:
            dt: A datetime object.

        Returns:
            A string formatted as YYYY-MM-DD HH:MM:SS.
        """
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _get_instance_start_date(instance: Instance) -> datetime.datetime:
        """Retrieves the start date of an instance or returns a minimal datetime.

        Args:
            instance: An Instance object.

        Returns:
            The start date if present; otherwise, datetime.datetime.min.
        """
        return instance.start_date or datetime.datetime.min

    def format_bids(self, bids: List[Bid]) -> None:
        """Formats and prints a table of bids to the console.

        Args:
            bids: A list of Bid objects.
        """
        if not bids:
            self.logger.info("No bids found to display.")
            self.console.print("\n\nNo bids found.", style="bold yellow")
            return

        bid_table: Table = Table(
            title="Current Bids",
            title_style="bold",
            header_style="bold",
            border_style="dim",
        )
        bid_table.add_column("Name", style="#1E90FF")
        bid_table.add_column("Type", style="#00BFFF")
        bid_table.add_column("Quantity", style="#87CEFA")
        bid_table.add_column("Created", style="#ADD8E6")
        # TODO: Enable region when available.
        # bid_table.add_column("Region", style="yellow")
        bid_table.add_column("Status", style="#4169E1")

        for bid in bids[: self.max_rows]:
            quantity_str: str = self._safe_format(bid.instance_quantity)
            created_str: str = (
                self._safe_format(bid.created_at, formatter=self._format_datetime)
                if isinstance(bid.created_at, datetime.datetime)
                else self._safe_format(bid.created_at)
            )
            # Compute region_str for potential future use.
            _ = self._safe_format(getattr(bid, "region", None))
            bid_table.add_row(
                self._safe_format(bid.name),
                self._safe_format(bid.instance_type_id),
                quantity_str,
                created_str,
                # region_str,  # Uncomment when region is supported.
                self._safe_format(bid.status),
            )

        self.logger.debug("Displaying %d bid rows.", min(len(bids), self.max_rows))
        self.console.print(bid_table)

    def format_instances(self, instances: List[Instance]) -> None:
        """Formats and prints a table of instances to the console.

        Args:
            instances: A list of Instance objects.
        """
        if not instances:
            self.logger.info("No instances found to display.")
            self.console.print("No instances found.", style="bold yellow")
            return

        # Sort instances by start date in descending order.
        instances_sorted: List[Instance] = sorted(
            instances,
            key=self._get_instance_start_date,
            reverse=True,
        )

        instance_table: Table = Table(
            title="Current Instances",
            title_style="bold",
            header_style="bold",
            border_style="dim",
        )
        instance_table.add_column("Name", style="#1E90FF")
        instance_table.add_column("Type", style="#00BFFF")
        instance_table.add_column("Status", style="#87CEFA")
        instance_table.add_column("Created", style="#ADD8E6")
        # TODO: Enable region when available.
        # instance_table.add_column("Region", style="yellow")
        instance_table.add_column("IP Address", style="#4169E1")
        instance_table.add_column("Category", style="#6495ED")

        for instance in instances_sorted[: self.max_rows]:
            created_str: str = (
                self._safe_format(
                    instance.start_date,
                    default=MISSING_VALUE,
                    formatter=self._format_datetime,
                )
                if instance.start_date
                else MISSING_VALUE
            )
            instance_table.add_row(
                self._safe_format(instance.name),
                self._safe_format(
                    getattr(instance, "instance_type_id", None),
                    default="Unknown",
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
        self.console.print(instance_table)
