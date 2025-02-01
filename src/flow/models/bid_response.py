from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, field_validator
import uuid


class BidResponse(BaseModel):
    """Represents what the server sends back after a successful place_bid."""

    id: str
    name: Optional[str] = None
    cluster_id: str
    instance_quantity: int
    instance_type_id: str
    limit_price_cents: int
    project_id: Optional[str] = None
    user_id: Optional[str] = None
    disk_ids: Optional[List[str]] = None
    created_at: Optional[datetime] = None
    deactivated_at: Optional[datetime] = None

    @field_validator("id", "cluster_id", "instance_type_id")
    def not_empty_fields(cls, value: str) -> str:
        """Raises ValueError if the field is empty or whitespace."""
        if not value.strip():
            raise ValueError("Field cannot be empty or whitespace")
        return value

    @classmethod
    def dummy_response(
        cls,
        order_name: str,
        project_id: Optional[str] = None,
        user_id: Optional[str] = None,
        disk_ids: Optional[List[str]] = None,
        cluster_id: Optional[str] = "unknown",
        instance_quantity: Optional[int] = 1,
        instance_type_id: Optional[str] = "unknown",
        limit_price_cents: Optional[int] = 0,
    ) -> "BidResponse":
        """
        Construct a dummy BidResponse with required fields populated.

        This method encapsulates our default/fallback logic for creating a BidResponse
        when we detect an idempotent duplicate bid.
        """
        return cls(
            id=str(uuid.uuid4()),
            name=order_name,
            cluster_id=cluster_id,
            instance_quantity=instance_quantity,
            instance_type_id=instance_type_id,
            limit_price_cents=limit_price_cents,
            project_id=project_id,
            user_id=user_id,
            disk_ids=disk_ids or [],
            created_at=None,
            deactivated_at=None,
        )
