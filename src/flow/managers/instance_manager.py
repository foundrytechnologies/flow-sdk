import logging
from typing import Dict, List, Optional, Type, Union

from flow.clients.foundry_client import FoundryClient
from flow.models import (
    Instance,
    SpotInstance,
    ReservedInstance,
    LegacyInstance,
    BlockInstance,
    ControlInstance,
)


class InstanceManager:
    """Manages retrieval and filtering of instances."""

    CATEGORY_CLASS_MAPPING: Dict[str, Type[Instance]] = {
        "spot": SpotInstance,
        "reserved": ReservedInstance,
        "legacy": LegacyInstance,
        "blocks": BlockInstance,
        "control": ControlInstance,
    }

    def __init__(self, foundry_client: FoundryClient):
        """Initializes InstanceManager.

        Args:
            foundry_client: A FoundryClient used for retrieving instance data.
        """
        self.foundry_client = foundry_client
        self.logger = logging.getLogger(self.__class__.__name__)

    def get_instances(self, project_id: str) -> List[Instance]:
        """Gets a flattened list of Instances from the foundry client.

        Retrieves a mapping of category to items from FoundryClient, which may be
        raw dictionaries or Pydantic instance objects. This method flattens those
        items into a single list of Instances.

        Args:
            project_id: The project identifier string.

        Returns:
            A list of Instances retrieved from the foundry client.
        """
        instances_by_category = self.foundry_client.get_instances(project_id=project_id)
        if not instances_by_category:
            return []

        flattened: List[Instance] = []
        for category, items in instances_by_category.items():
            for item in items:
                instance = self._create_instance_from_dict(item, category)
                flattened.append(instance)
        return flattened

    def _create_instance_from_dict(
        self, item: Union[dict, Instance], category: str
    ) -> Instance:
        """Converts data into an Instance with a designated category.

        If `item` is a dictionary, a new Instance object is instantiated with
        the provided category. If `item` is already an Instance, it is
        re-instantiated to ensure that the `category` field is set correctly.

        Args:
            item: A dictionary or an existing Instance.
            category: The category name to associate with the resulting Instance.

        Returns:
            An Instance object whose type is determined by `CATEGORY_CLASS_MAPPING`.
        """
        instance_class = self.CATEGORY_CLASS_MAPPING.get(category, Instance)
        if isinstance(item, dict):
            item["category"] = category
            if instance_class is Instance:
                self.logger.warning(
                    "Unknown instance category '%s'. Defaulting to base Instance.",
                    category,
                )
            return instance_class(**item)
        else:
            inst_data = item.model_dump()
            inst_data["category"] = category
            if instance_class is Instance:
                self.logger.warning(
                    "Unknown instance category '%s'. Defaulting to base Instance.",
                    category,
                )
            return instance_class(**inst_data)

    def filter_instances(
        self,
        instances: List[Instance],
        name: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Instance]:
        """Filters a list of instances by name and status.

        Args:
            instances: The list of Instance objects to filter.
            name: An optional string to match an instance's name.
            status: An optional string to match an instance's status.

        Returns:
            A filtered list of Instance objects that match the provided criteria.
        """
        filtered = instances
        if name:
            filtered = [inst for inst in filtered if inst.name == name]
        if status:
            filtered = [inst for inst in filtered if inst.instance_status == status]
        return filtered

    # TODO(jaredquincy): Circle back to add more methods as needed (e.g., terminate_instance, get_instance_by_id, etc.)
    #                   Currently, this is focusing on getting, filtering, and helping display instances for the prelimiinary CLI.
