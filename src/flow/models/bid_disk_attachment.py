from pydantic import BaseModel, field_validator

from flow.models.disk_attachment import DiskAttachment


class BidDiskAttachment(BaseModel):
    """Represents a disk attachment in a bid.

    Includes fields required by the bid API.

    Attributes:
        disk_id: The unique identifier for the disk.
        volume_name: The display name of the disk (as expected by the API).
    """

    disk_id: str
    volume_name: str

    @field_validator("disk_id", "volume_name")
    def validate_non_empty_fields(cls, value: str) -> str:
        """Checks that a field is not empty or whitespace.

        Args:
            value: The field value to validate.

        Raises:
            ValueError: If the field is empty or contains only whitespace.

        Returns:
            The validated field value.
        """
        if not value or not value.strip():
            raise ValueError("Field cannot be empty or whitespace.")
        return value

    @classmethod
    def from_disk_attachment(
        cls, disk_attachment: DiskAttachment
    ) -> "BidDiskAttachment":
        """Creates a BidDiskAttachment from a DiskAttachment.

        Args:
            disk_attachment: A DiskAttachment instance to convert.

        Returns:
            A BidDiskAttachment instance.
        """
        return cls(
            disk_id=disk_attachment.disk_id,
            volume_name=disk_attachment.volume_name or disk_attachment.name,
        )
