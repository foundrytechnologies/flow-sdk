from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, model_validator


class BidResponse(BaseModel):
    id: str
    order_name: str
    status: str
    cluster_id: Optional[str] = None
    instance_quantity: Optional[int] = None
    instance_type_id: Optional[str] = None
    limit_price_cents: Optional[int] = None
    disk_ids: List[str] = []
    created_at: Optional[datetime] = None
    deactivated_at: Optional[datetime] = None

    @model_validator(mode="before")
    @classmethod
    def handle_duplicate_status(cls, values):
        if values.get("status") == "duplicate":
            values.setdefault("cluster_id", None)
            values.setdefault("instance_quantity", None)
            values.setdefault("instance_type_id", None)
            values.setdefault("limit_price_cents", None)
        return values
