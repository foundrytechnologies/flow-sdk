from abc import ABC, abstractmethod
from typing import Any, Dict, List

from rich.console import Console


class Formatter(ABC):
    """Base class for formatters.

    This abstract class provides a rich console for generating formatted text
    output. It also defines an abstract method that subclasses must implement
    to format and display the status of bids and instances.

    Attributes:
        console (Console): A rich console used to output formatted text.
    """

    def __init__(self) -> None:
        """Initializes a Formatter instance with a rich console for output.

        The console is used by subclasses to display rich text.
        """
        self.console = Console()

    @abstractmethod
    def format_status(
        self, bids: List[Dict[str, Any]], instances: List[Dict[str, Any]]
    ) -> None:
        """Formats and displays the status of bids and instances.

        Subclasses must override this method to provide the required formatting
        of bid and instance data.

        Args:
            bids: A list of dictionaries containing bid information.
            instances: A list of dictionaries containing instance information.

        Raises:
            NotImplementedError: Always raised unless overridden by a subclass.
        """
        raise NotImplementedError("Subclasses must implement this method.")
