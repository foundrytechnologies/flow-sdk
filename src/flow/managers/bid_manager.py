"""
BidManager Module

Key Components:
  - IBidClient: An abstract interface for bid submission clients.
  - BidPayloadBuilder: Constructs and validates a BidPayload from given parameters.
  - BidSubmitter: Encapsulates bid submission logic (with potential for retry logic).
  - ChunkedBidEngine: Splits a large bid request into smaller chunks and submits them,
      optionally customizing the startup script per chunk.
  - BidManager: The public façade that composes the above components.

Usage Examples:

  # --- Single-Bid (all-or-nothing) mode ---
  payload = bid_manager.prepare_bid_payload(
      cluster_id="cluster-123",
      instance_quantity=10,
      instance_type_id="instance-xyz",
      limit_price_cents=1000,
      order_name="my-job",
      project_id="proj-123",
      ssh_key_id="ssh-abc",
      user_id="user-123",
      startup_script="echo 'Starting instance...'"
  )
  bids = bid_manager.submit_bid(project_id="proj-123", bid_payload=payload)
  # bids is a list with one Bid object.

  # --- Partial Fulfillment (chunked) mode ---
  def custom_script(chunk_idx: int, base_script: Optional[str]) -> Optional[str]:
      # Example: Append an environment variable for the chunk index.
      return (base_script or "") + f"\nexport CHUNK_INDEX={chunk_idx}"

  bids = bid_manager.submit_bid(
      project_id="proj-123",
      cluster_id="cluster-123",
      instance_quantity=100,
      instance_type_id="instance-xyz",
      limit_price_cents=500,
      order_name="large-job",
      ssh_key_id="ssh-abc",
      user_id="user-123",
      startup_script="echo 'Starting chunk...'",
      disk_attachments=[...],
      allow_partial_fulfillment=True,
      chunk_size=5,
      startup_script_customizer=custom_script,
  )
  # bids will be a list of 20 Bid objects (100/5).

All components are fully type annotated and documented.
"""

from __future__ import annotations

import logging
import uuid
from typing import Callable, List, Optional, Protocol, Union

from pydantic import BaseModel, Field, ValidationError

# Imports from our codebase.
from flow.clients.foundry_client import FoundryClient
from flow.logging.spinner_logger import SpinnerLogger
from flow.models import Bid, BidPayload, DiskAttachment, BidDiskAttachment


# -----------------------------------------------------------------------------
# Domain-Specific Exceptions
# -----------------------------------------------------------------------------
class BidSubmissionError(Exception):
    """Raised when bid submission fails."""


class InvalidChunkSizeError(ValueError):
    """Raised when the provided chunk size for partial bids is invalid."""


# -----------------------------------------------------------------------------
# IBidClient Interface
# -----------------------------------------------------------------------------
class IBidClient(Protocol):
    """Protocol defining the contract for a bid submission client."""

    def place_bid(self, project_id: str, bid_payload: BidPayload) -> Bid: ...


# -----------------------------------------------------------------------------
# Partial Bid Parameters Model
# -----------------------------------------------------------------------------
class PartialBidParams(BaseModel):
    """
    Parameters for submitting a partial (chunked) bid.

    Attributes:
        cluster_id: The target cluster for the bid.
        instance_quantity: Total number of instances to request.
        instance_type_id: The instance type identifier.
        limit_price_cents: The maximum price (in cents) per instance.
        order_name: Base order name for the bid (a suffix will be appended per chunk).
        ssh_key_id: The SSH key identifier.
        user_id: The identifier of the user submitting the bid.
        startup_script: Optional base startup script.
        disk_attachments: Optional list of disk attachments.
        chunk_size: Number of instances per partial bid chunk.
    """

    cluster_id: str = Field(..., min_length=1)
    instance_quantity: int = Field(..., gt=0)
    instance_type_id: str = Field(..., min_length=1)
    limit_price_cents: int = Field(..., gt=0)
    order_name: str = Field(..., min_length=1)
    ssh_key_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    startup_script: Optional[str] = None
    disk_attachments: Optional[List[DiskAttachment]] = None
    chunk_size: int = Field(1, gt=0)


# -----------------------------------------------------------------------------
# BidPayloadBuilder: Constructs BidPayload instances.
# -----------------------------------------------------------------------------
class BidPayloadBuilder:
    def __init__(self) -> None:
        self.logger: logging.Logger = logging.getLogger(self.__class__.__name__)

    def build(
        self,
        *,
        cluster_id: str,
        instance_quantity: int,
        instance_type_id: str,
        limit_price_cents: int,
        order_name: str,
        project_id: str,
        ssh_key_id: str,
        user_id: str,
        startup_script: Optional[str] = None,
        disk_attachments: Optional[List[DiskAttachment]] = None,
    ) -> BidPayload:
        """Constructs and validates a BidPayload from the provided parameters.

        Returns:
            A validated BidPayload instance.

        Raises:
            ValidationError: If the constructed payload fails validation.
        """
        self.logger.debug("Building BidPayload for order_name=%s", order_name)
        try:
            bid_disk_attachments: List[BidDiskAttachment] = []
            if disk_attachments:
                for da in disk_attachments:
                    bid_disk_attachments.append(
                        BidDiskAttachment.from_disk_attachment(da)
                    )
            payload = BidPayload(
                cluster_id=cluster_id,
                instance_quantity=instance_quantity,
                instance_type_id=instance_type_id,
                limit_price_cents=limit_price_cents,
                order_name=order_name,
                project_id=project_id,
                ssh_key_ids=[ssh_key_id],
                user_id=user_id,
                startup_script=startup_script,
                disk_attachments=bid_disk_attachments,
            )
            return payload
        except ValidationError as err:
            self.logger.error("Error building BidPayload: %s", err)
            raise


# -----------------------------------------------------------------------------
# BidSubmitter: Submits a bid using an IBidClient.
# -----------------------------------------------------------------------------
class BidSubmitter:
    def __init__(self, bid_client: IBidClient) -> None:
        self.bid_client = bid_client
        self.logger: logging.Logger = logging.getLogger(self.__class__.__name__)

    def submit(self, project_id: str, payload: BidPayload) -> Bid:
        """Submits a single bid using the bid client.

        Returns:
            The Bid object returned by the client.

        Raises:
            BidSubmissionError: If the bid submission fails.
        """
        self.logger.debug("Submitting bid with order_name=%s", payload.order_name)
        try:
            bid = self.bid_client.place_bid(project_id=project_id, bid_payload=payload)
            self.logger.info("Bid submitted successfully: bid_id=%s", bid.id)
            return bid
        except Exception as e:
            self.logger.error("Bid submission failed: %s", e, exc_info=True)
            raise BidSubmissionError("Bid submission failed") from e


# -----------------------------------------------------------------------------
# ChunkedBidEngine: Splits a large bid request into smaller chunks.
# -----------------------------------------------------------------------------
class ChunkedBidEngine:
    def __init__(
        self,
        payload_builder: BidPayloadBuilder,
        submitter: BidSubmitter,
        startup_script_customizer: Optional[
            Callable[[int, Optional[str]], Optional[str]]
        ] = None,
    ) -> None:
        """
        Args:
            payload_builder: Instance for building bid payloads.
            submitter: Instance for submitting bids.
            startup_script_customizer: Optional callback to customize the startup script per chunk.
                The callback signature should be:
                (chunk_index: int, base_script: Optional[str]) -> Optional[str]
        """
        self.payload_builder = payload_builder
        self.submitter = submitter
        self.startup_script_customizer = startup_script_customizer
        self.logger: logging.Logger = logging.getLogger(self.__class__.__name__)

    def submit_chunks(self, project_id: str, params: PartialBidParams) -> List[Bid]:
        """Splits the total instance_quantity into chunks and submits a bid for each chunk.

        Returns:
            A list of Bid objects (one per chunk).

        Raises:
            BidSubmissionError: If any chunk submission fails.
        """
        submission_id = uuid.uuid4().hex
        self.logger.info(
            "Starting partial bid submission (submission_id=%s): total=%d, chunk_size=%d, order_name=%s",
            submission_id,
            params.instance_quantity,
            params.chunk_size,
            params.order_name,
        )
        bids_submitted: List[Bid] = []
        remaining = params.instance_quantity
        chunk_index = 1

        while remaining > 0:
            current_chunk = min(params.chunk_size, remaining)
            chunk_order_name = f"{params.order_name}-chunk{chunk_index}"

            if self.startup_script_customizer:
                chunk_script = self.startup_script_customizer(
                    chunk_index, params.startup_script
                )
            else:
                chunk_script = params.startup_script

            payload = self.payload_builder.build(
                cluster_id=params.cluster_id,
                instance_quantity=current_chunk,
                instance_type_id=params.instance_type_id,
                limit_price_cents=params.limit_price_cents,
                order_name=chunk_order_name,
                project_id=project_id,
                ssh_key_id=params.ssh_key_id,
                user_id=params.user_id,
                startup_script=chunk_script,
                disk_attachments=params.disk_attachments,
            )

            self.logger.info(
                "Submitting chunk #%d: %d instances, order_name=%s (submission_id=%s)",
                chunk_index,
                current_chunk,
                chunk_order_name,
                submission_id,
            )
            bid = self.submitter.submit(project_id=project_id, payload=payload)
            bids_submitted.append(bid)
            self.logger.info(
                "Chunk #%d submitted successfully (bid_id=%s), remaining=%d (submission_id=%s)",
                chunk_index,
                bid.id,
                remaining - current_chunk,
                submission_id,
            )
            remaining -= current_chunk
            chunk_index += 1

        self.logger.info(
            "Partial bid submission complete (submission_id=%s). Total chunks: %d",
            submission_id,
            len(bids_submitted),
        )
        return bids_submitted


# -----------------------------------------------------------------------------
# BidManager: Public façade for bid submission.
# -----------------------------------------------------------------------------
class BidManager:
    """Public interface for bid submission. Supports both single-bid (all-or-nothing)
    and partial (chunked) bid submission modes.
    """

    def __init__(self, foundry_client: FoundryClient) -> None:
        """
        Args:
            foundry_client: A FoundryClient instance (which implements IBidClient)
                for interacting with the Foundry API.
        """
        self.foundry_client: FoundryClient = foundry_client
        self.logger: logging.Logger = logging.getLogger(self.__class__.__name__)
        self.spinner_logger = SpinnerLogger(self.logger)
        self.payload_builder = BidPayloadBuilder()
        self.bid_submitter = BidSubmitter(bid_client=foundry_client)
        # By default, initialize without a custom startup script customizer.
        self.chunked_engine = ChunkedBidEngine(
            payload_builder=self.payload_builder, submitter=self.bid_submitter
        )

    def prepare_bid_payload(
        self,
        *,
        cluster_id: str,
        instance_quantity: int,
        instance_type_id: str,
        limit_price_cents: int,
        order_name: str,
        project_id: str,
        ssh_key_id: str,
        user_id: str,
        startup_script: Optional[str] = None,
        disk_attachments: Optional[List[DiskAttachment]] = None,
    ) -> BidPayload:
        """Prepare a validated BidPayload from the provided parameters.

        Args:
            cluster_id (str): The target cluster.
            instance_quantity (int): Number of instances requested.
            instance_type_id (str): The instance type identifier.
            limit_price_cents (int): Maximum price (in cents) per instance.
            order_name (str): A descriptive bid name.
            project_id (str): The Foundry project ID.
            ssh_key_id (str): SSH key identifier.
            user_id (str): The user submitting the bid.
            startup_script (Optional[str]): Optional startup script.
            disk_attachments (Optional[List[DiskAttachment]]): Optional list of disk attachments.

        Returns:
            BidPayload: A validated BidPayload object.

        Raises:
            ValidationError: If payload construction fails.
        """
        return self.payload_builder.build(
            cluster_id=cluster_id,
            instance_quantity=instance_quantity,
            instance_type_id=instance_type_id,
            limit_price_cents=limit_price_cents,
            order_name=order_name,
            project_id=project_id,
            ssh_key_id=ssh_key_id,
            user_id=user_id,
            startup_script=startup_script,
            disk_attachments=disk_attachments,
        )

    def submit_bid(
        self,
        *,
        project_id: str,
        bid_payload: Optional[BidPayload] = None,
        cluster_id: Optional[str] = None,
        instance_quantity: Optional[int] = None,
        instance_type_id: Optional[str] = None,
        limit_price_cents: Optional[int] = None,
        order_name: Optional[str] = None,
        ssh_key_id: Optional[str] = None,
        user_id: Optional[str] = None,
        startup_script: Optional[str] = None,
        disk_attachments: Optional[List[DiskAttachment]] = None,
        allow_partial_fulfillment: bool = False,
        chunk_size: int = 1,
        startup_script_customizer: Optional[
            Callable[[int, Optional[str]], Optional[str]]
        ] = None,
    ) -> List[Bid]:
        """Submit bid(s) to Foundry using either single-bid or partial (chunked) mode.

        In single-bid mode (when a pre-built bid_payload is provided or
        allow_partial_fulfillment is False), one bid is submitted.
        In partial mode, the total instance_quantity is split into chunks of size chunk_size,
        and each chunk is submitted as a separate bid.

        Args:
            project_id (str): The Foundry project ID.
            bid_payload (Optional[BidPayload]): Pre-built BidPayload for single-bid submission.
            cluster_id (Optional[str]): Cluster ID (required for partial mode).
            instance_quantity (Optional[int]): Total instances requested (required for partial mode).
            instance_type_id (Optional[str]): Instance type identifier (required for partial mode).
            limit_price_cents (Optional[int]): Limit price in cents (required for partial mode).
            order_name (Optional[str]): Base name for the bid(s) (required for partial mode).
            ssh_key_id (Optional[str]): SSH key ID (required for partial mode).
            user_id (Optional[str]): User ID (required for partial mode).
            startup_script (Optional[str]): Optional base startup script.
            disk_attachments (Optional[List[DiskAttachment]]): Optional disk attachments.
            allow_partial_fulfillment (bool): If True, submits multiple partial bids.
            chunk_size (int): Number of instances per partial bid chunk.
            startup_script_customizer (Optional[Callable[[int, Optional[str]], Optional[str]]]):
                Optional callback for per-chunk startup script customization.

        Returns:
            List[Bid]: A list of Bid objects (one for single-bid or multiple for partial mode).

        Raises:
            ValueError: If required parameters for partial mode are missing.
            ValidationError: If parameter validation fails.
        """
        if bid_payload:
            self.logger.info("Submitting single bid with pre-built payload.")
            return self._submit_single_bid(
                project_id=project_id, bid_payload=bid_payload
            )

        if not allow_partial_fulfillment:
            # Single-bid mode using parameters.
            if not all(
                [
                    cluster_id,
                    instance_quantity,
                    instance_type_id,
                    limit_price_cents,
                    order_name,
                    ssh_key_id,
                    user_id,
                ]
            ):
                raise ValueError(
                    "Missing required parameters for single-bid submission."
                )
            single_payload = self.payload_builder.build(
                cluster_id=cluster_id,
                instance_quantity=instance_quantity,
                instance_type_id=instance_type_id,
                limit_price_cents=limit_price_cents,
                order_name=order_name,
                project_id=project_id,
                ssh_key_id=ssh_key_id,
                user_id=user_id,
                startup_script=startup_script,
                disk_attachments=disk_attachments,
            )
            return self._submit_single_bid(
                project_id=project_id, bid_payload=single_payload
            )

        # Partial fulfillment mode.
        try:
            partial_params = PartialBidParams(
                cluster_id=cluster_id,  # type: ignore
                instance_quantity=instance_quantity,  # type: ignore
                instance_type_id=instance_type_id,  # type: ignore
                limit_price_cents=limit_price_cents,  # type: ignore
                order_name=order_name,  # type: ignore
                ssh_key_id=ssh_key_id,  # type: ignore
                user_id=user_id,  # type: ignore
                startup_script=startup_script,
                disk_attachments=disk_attachments,
                chunk_size=chunk_size,
            )
        except ValidationError as e:
            self.logger.error("Partial bid parameters validation error: %s", e)
            raise ValueError("Invalid partial bid parameters") from e

        # Update the ChunkedBidEngine with the custom startup script callback if provided.
        self.chunked_engine = ChunkedBidEngine(
            payload_builder=self.payload_builder,
            submitter=self.bid_submitter,
            startup_script_customizer=startup_script_customizer,
        )
        return self.chunked_engine.submit_chunks(
            project_id=project_id, params=partial_params
        )

    def get_bids(self, *, project_id: str) -> List[Bid]:
        """Retrieve all bids for a given project.

        Args:
            project_id (str): The Foundry project ID.

        Returns:
            List[Bid]: A list of Bid objects.
        """
        self.logger.debug("Retrieving bids for project_id=%s", project_id)
        return self.foundry_client.get_bids(project_id=project_id)

    def cancel_bid(self, *, project_id: str, bid_id: str) -> None:
        """Cancel a bid given its ID.

        Args:
            project_id (str): The Foundry project ID.
            bid_id (str): The unique identifier of the bid to cancel.
        """
        self.logger.info(
            "Canceling bid with ID=%s in project_id=%s", bid_id, project_id
        )
        self.foundry_client.cancel_bid(project_id=project_id, bid_id=bid_id)
        self.logger.info("Bid with ID=%s canceled successfully.", bid_id)

    def _submit_single_bid(
        self, *, project_id: str, bid_payload: BidPayload
    ) -> List[Bid]:
        """Submit a single bid and return it in a list.

        Args:
            project_id (str): The Foundry project ID.
            bid_payload (BidPayload): The validated BidPayload.

        Returns:
            List[Bid]: A list containing a single Bid object.
        """
        self.logger.info(
            "Submitting single bid with order_name=%s", bid_payload.order_name
        )
        bid = self.foundry_client.place_bid(
            project_id=project_id, bid_payload=bid_payload
        )
        # Check if returned bid is already a list
        if isinstance(bid, list):
            self.logger.info("Single bid submitted: bid_id=%s", bid[0].id)
            return bid
        else:
            self.logger.info("Single bid submitted: bid_id=%s", bid.id)
            return [bid]
