"""Auction finder and matcher logic, with optional catalog enrichment if API response is incomplete.

This is a core module, so expanding on the structure -- this module provides the following primary classes:

- `AuctionMatcher`: Encapsulates logic to check whether an `Auction` meets
  certain resource criteria (via a `ResourcesSpecification`).
- `AuctionFinder`: Fetches auctions from multiple sources (Foundry and/or a local
  YAML-based catalog) and optionally enriches them with static data. Also exposes
  a method to filter fetched auctions using an `AuctionMatcher`.

Example usage:

  from pathlib import Path
  from flow.clients.foundry_client import FoundryClient
  from flow.logging import spinner_logger
  from flow.task_config.models import ResourcesSpecification
  from your_project.auction_finder import AuctionFinder

  foundry_client = FoundryClient()
  logger = spinner_logger.get_logger()

  finder = AuctionFinder(
      foundry_client=foundry_client,
      logger_obj=logger,
      local_catalog_path=Path("my_auction_catalog.yaml"),
  )

  # Fetch from both Foundry (by project_id) and local file.
  auctions = finder.fetch_auctions(
      project_id="some-project-id",
      local_catalog_path="my_auction_catalog.yaml"
  )

  # Filter auctions by desired specs.
  criteria = ResourcesSpecification(gpu_type="NVIDIA A100", num_gpus=2)
  matching_auctions = finder.find_matching_auctions(
      auctions=auctions, criteria=criteria
  )

  # matching_auctions now contains all the auctions that satisfy your specs.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

from flow.clients.foundry_client import FoundryClient
from flow.models import Auction
from flow.task_config.models import ResourcesSpecification


logger = logging.getLogger(__name__)


class AuctionCatalogError(Exception):
    """Raised when there's an error loading or parsing a local auction catalog."""


class AuctionMatcher:
    """Checks whether an `Auction` matches a given `ResourcesSpecification`."""

    def __init__(
        self,
        *,
        criteria: ResourcesSpecification,
        logger_obj: logging.Logger,
    ) -> None:
        """Initializes the AuctionMatcher.

        Args:
            criteria: A `ResourcesSpecification` containing desired resource fields.
            logger_obj: A logger for debug messages.
        """
        self._criteria = criteria
        self._logger = logger_obj

    def matches(self, auction: Auction) -> bool:
        """Returns True if the `Auction` satisfies the criteria, otherwise False.

        Args:
            auction: The `Auction` instance to check.

        Returns:
            True if the auction meets the specification, False otherwise.
        """
        # Gather all checks with their results and a descriptive message
        check_results = [
            self._check_gpu_type(auction),
            self._check_num_gpus(auction),
            self._check_internode_interconnect(auction),
            self._check_intranode_interconnect(auction),
            self._check_fcp_instance(auction),
        ]

        # Filter out only those checks that failed
        failing_checks = [res for res in check_results if not res["passed"]]

        if failing_checks:
            # Group them in a more detailed log.
            # For cleanliness, each Auction's failing checks are aggregated below.
            self._logger.debug(
                "Auction %s (%s) failed the following criteria checks:\n  - %s",
                auction.cluster_id,
                auction,
                "\n  - ".join(
                    f"{check['name']}: {check['detail']}" for check in failing_checks
                ),
            )
            return False

        return True

    def _check_gpu_type(self, auction: Auction) -> dict:
        """Checks GPU type matches if specified. Returns a dict of check details."""
        if not self._criteria.gpu_type:
            return {
                "name": "GPU Type",
                "passed": True,
                "detail": "No GPU type specified in criteria; skipping check.",
            }

        expected = self._criteria.gpu_type.strip().lower()
        actual = (auction.gpu_type or "").lower()
        pattern = rf"\b{re.escape(expected)}\b"

        passed = bool(re.search(pattern, actual))
        detail = (
            f"Expected GPU type to match '{self._criteria.gpu_type}' but got '{auction.gpu_type}'."
            if not passed
            else f"GPU type '{auction.gpu_type}' matches expected pattern '{self._criteria.gpu_type}'."
        )

        return {"name": "GPU Type", "passed": passed, "detail": detail}

    def _check_num_gpus(self, auction: Auction) -> dict:
        """Checks if the auction has at least the requested number of GPUs."""
        if self._criteria.num_gpus is None:
            return {
                "name": "Number of GPUs",
                "passed": True,
                "detail": "No GPU count specified in criteria; skipping check.",
            }

        actual_gpus = auction.inventory_quantity or 0
        required_gpus = self._criteria.num_gpus
        passed = actual_gpus >= required_gpus

        detail = (
            f"Needed >= {required_gpus} GPUs, but auction has {actual_gpus}."
            if not passed
            else f"Auction has {actual_gpus} GPUs, which meets or exceeds the required {required_gpus}."
        )

        return {"name": "Number of GPUs", "passed": passed, "detail": detail}

    def _check_internode_interconnect(self, auction: Auction) -> dict:
        """Checks if the inter-node interconnect matches, if specified."""
        if not self._criteria.internode_interconnect:
            return {
                "name": "Inter-node Interconnect",
                "passed": True,
                "detail": "No inter-node interconnect specified; skipping check.",
            }

        expected = self._criteria.internode_interconnect.lower()
        actual = (auction.internode_interconnect or "").lower()
        passed = actual == expected

        detail = (
            f"Expected inter-node '{self._criteria.internode_interconnect}' but got '{auction.internode_interconnect}'."
            if not passed
            else f"Inter-node interconnect '{auction.internode_interconnect}' matches '{self._criteria.internode_interconnect}'."
        )

        return {"name": "Inter-node Interconnect", "passed": passed, "detail": detail}

    def _check_intranode_interconnect(self, auction: Auction) -> dict:
        """Checks if the intra-node interconnect matches, if specified."""
        if not self._criteria.intranode_interconnect:
            return {
                "name": "Intra-node Interconnect",
                "passed": True,
                "detail": "No intra-node interconnect specified; skipping check.",
            }

        expected = self._criteria.intranode_interconnect.lower()
        actual = (auction.intranode_interconnect or "").lower()
        passed = actual == expected

        detail = (
            f"Expected intra-node '{self._criteria.intranode_interconnect}' but got '{auction.intranode_interconnect}'."
            if not passed
            else f"Intra-node interconnect '{auction.intranode_interconnect}' matches '{self._criteria.intranode_interconnect}'."
        )

        return {"name": "Intra-node Interconnect", "passed": passed, "detail": detail}

    def _check_fcp_instance(self, auction: Auction) -> dict:
        """Checks if FCP instance matches exactly (case-sensitive) if specified."""
        if not self._criteria.fcp_instance:
            return {
                "name": "FCP Instance",
                "passed": True,
                "detail": "No FCP instance specified; skipping check.",
            }

        passed = auction.fcp_instance == self._criteria.fcp_instance
        detail = (
            f"Expected FCP instance '{self._criteria.fcp_instance}' but got '{auction.fcp_instance}'."
            if not passed
            else f"FCP instance '{auction.fcp_instance}' matches '{self._criteria.fcp_instance}'."
        )

        return {"name": "FCP Instance", "passed": passed, "detail": detail}


class AuctionFinder:
    """Loads auctions from multiple sources (Foundry and/or static YAML) and finds matches.

    Optionally enriches Foundry-fetched auctions with missing data from the local catalog
    (based on the instance_type_id or other matching keys).
    """

    def __init__(
        self,
        *,
        foundry_client: FoundryClient,
        logger_obj: Optional[logging.Logger] = None,
        local_catalog_path: Optional[Union[str, Path]] = None,
    ) -> None:
        """Initializes the AuctionFinder.

        Args:
            foundry_client: A `FoundryClient` instance for dynamic fetching of auctions.
            logger_obj: Optional logger. If None, uses module-level logger.
            local_catalog_path: Path to a local static YAML file of auctions (optional).
        """
        self._foundry_client = foundry_client
        self._logger = logger_obj or logger
        self.default_local_catalog_path = (
            Path(__file__).parents[3] / "fcp_auction_catalog.yaml"
        )

        if local_catalog_path is not None:
            self.local_catalog_path = Path(local_catalog_path)
        else:
            self.local_catalog_path = None

        # Optional debug so we see what's happening:
        if self.default_local_catalog_path.exists():
            self._logger.debug(
                "Default local catalog path: %s (exists=%s)",
                self.default_local_catalog_path,
                self.default_local_catalog_path.exists(),
            )
        else:
            self._logger.debug(
                "Default local catalog not found at: %s",
                self.default_local_catalog_path,
            )

        # If local_catalog_path was passed to constructor, attempt to load it now
        if self.local_catalog_path is not None:
            self._logger.debug(
                "Constructor using local_catalog_path=%s", self.local_catalog_path
            )
            self._load_instance_catalog()
        else:
            # Not specifying here. We'll rely on fetch_auctions() to pick up default
            self._logger.debug(
                "No local_catalog_path specified to constructor; will try default later."
            )

        self._instance_catalog: Dict[str, Any] = {}

    def fetch_auctions(
        self,
        *,
        project_id: Optional[str] = None,
        local_catalog_path: Optional[str] = None,
    ) -> List[Auction]:
        """Fetches auctions from Foundry or a local catalog file or both.

        Behavior:
          - If `project_id` is provided and no `local_catalog_path`, returns Foundry auctions only.
          - If `local_catalog_path` is provided and no `project_id`, returns local auctions only.
          - If both are provided, merges Foundry auctions with local catalog data.
          - If neither is provided, raises a ValueError.

        Args:
            project_id: Foundry project ID for fetching auctions from Foundry.
            local_catalog_path: Path to a local YAML file for static auctions.

        Returns:
            A list of `Auction` objects. If both Foundry and local data are used,
            returned items may be enriched with data from the static catalog.

        Raises:
            ValueError: If neither `project_id` nor `local_catalog_path` is provided.
            AuctionCatalogError: If there's a problem reading/parsing the local catalog.
        """
        auctions_from_foundry: List[Auction] = []
        auctions_from_local: List[Auction] = []

        # Possibly fetch from Foundry
        if project_id:
            self._logger.info(
                "Fetching auctions from Foundry for project_id=%s.", project_id
            )
            auctions_from_foundry = self._foundry_client.get_auctions(
                project_id=project_id
            )
            self._logger.info(
                "Foundry returned %d auctions.", len(auctions_from_foundry)
            )

        # Possibly load from a specified local catalog (overrides default if given)
        if local_catalog_path is not None:
            self._logger.info(
                "Loading auctions from local catalog at '%s'.", local_catalog_path
            )
            auctions_from_local = self._load_auctions_from_local_catalog(
                catalog_path=local_catalog_path
            )
            self._logger.info(
                "Local catalog has %d auctions total.", len(auctions_from_local)
            )
        elif self.default_local_catalog_path.exists():
            self._logger.info(
                "Loading auctions from default local catalog at '%s'.",
                self.default_local_catalog_path,
            )
            auctions_from_local = self._load_auctions_from_local_catalog(
                catalog_path=self.default_local_catalog_path
            )
            self._logger.info(
                "Default local catalog has %d auctions total.", len(auctions_from_local)
            )
        else:
            self._logger.info("No local catalog provided.")

        # Merge data if both sources are present
        if auctions_from_foundry and auctions_from_local:
            return self._enrich_auctions_with_catalog_data(
                foundry_auctions=auctions_from_foundry,
                local_catalog_auctions=auctions_from_local,
            )
        elif auctions_from_foundry:
            return auctions_from_foundry
        elif auctions_from_local:
            return auctions_from_local

        # If no data sources were provided, raise an error
        raise ValueError(
            "You must provide either 'project_id' to fetch from Foundry "
            "or 'local_catalog_path' to load a static catalog, or both."
        )

    def find_matching_auctions(
        self,
        *,
        auctions: List[Auction],
        criteria: ResourcesSpecification,
    ) -> List[Auction]:
        """Filters a list of Auctions to those matching the given `ResourcesSpecification`.

        Args:
            auctions: A list of `Auction` objects.
            criteria: The desired resource specification.

        Returns:
            A list of auctions that meet all the criteria.
        """
        self._logger.debug(
            "Filtering %d auctions with criteria: %s", len(auctions), criteria
        )

        matcher = AuctionMatcher(criteria=criteria, logger_obj=self._logger)
        matching_auctions = [
            auction for auction in auctions if matcher.matches(auction)
        ]

        self._logger.debug(
            "Found %d matching auctions (of %d total).",
            len(matching_auctions),
            len(auctions),
        )
        return matching_auctions

    def _resolve_local_catalog_path(
        self,
        *,
        path: Optional[Path],
    ) -> Optional[Path]:
        """Resolves a valid path to the local auction catalog, if any.

        If `path` is None, attempts to use a default path:
          `<repo_root>/my_auction_catalog.yaml`.

        Args:
            path: The user-specified `Path` to the local catalog or None.

        Returns:
            A `Path` object if the file exists, otherwise None. Logs warnings as needed.
        """
        if path is not None:
            if path.exists():
                return path
            self._logger.warning(
                "Local catalog path specified (%s), but file does not exist. "
                "Auction augmentation will proceed without local catalog.",
                path,
            )
            return None

        # If no path is provided, try the default location
        default_path = Path(__file__).parent.parent.parent / "my_auction_catalog.yaml"
        if default_path.exists():
            self._logger.debug(
                "No local_catalog_path specified; using default path: %s", default_path
            )
            return default_path

        self._logger.debug(
            "No local catalog provided, and default path %s does not exist. "
            "Auction augmentation will proceed without a local catalog.",
            default_path,
        )
        return None

    def _load_instance_catalog(self) -> None:
        """Loads the local instance catalog from `_local_catalog_path` into memory."""
        if not self.local_catalog_path:
            return

        try:
            with open(self.local_catalog_path, "r", encoding="utf-8") as f:
                self._instance_catalog = yaml.safe_load(f) or {}
            self._logger.info(
                "Successfully loaded local catalog from %s",
                self.local_catalog_path,
            )
        except Exception as exc:
            self._logger.error(
                "Failed to load local catalog from %s: %s",
                self.local_catalog_path,
                exc,
            )
            raise AuctionCatalogError(
                f"Unable to parse local catalog at {self.local_catalog_path}"
            ) from exc

    def _load_auctions_from_local_catalog(self, *, catalog_path: str) -> List[Auction]:
        """Loads auctions from a local static YAML file.

        Args:
            catalog_path: File path to the YAML catalog.

        Returns:
            A flat list of `Auction` objects parsed from the YAML.

        Raises:
            AuctionCatalogError: If there's any issue reading or parsing the catalog.
        """
        try:
            with open(catalog_path, "r", encoding="utf-8") as file:
                raw_data = yaml.safe_load(file) or {}
        except OSError as exc:
            msg = f"Unable to read local catalog file: {catalog_path}"
            self._logger.error(msg, exc_info=True)
            raise AuctionCatalogError(msg) from exc

        all_auctions: List[Auction] = []

        # Structure is generally:
        # {
        #   'nvidia a100': {
        #       'eu-central1-a': [
        #           {
        #               'base_auction': {...},
        #               'detailed_instance_data': {...}, ...
        #           },
        #           ...
        #       ],
        #       ...
        #   },
        #   'nvidia a40': {...},
        #   ...
        # }
        for gpu_label, region_map in raw_data.items():
            # Each `gpu_label` typically maps to a dict of region -> list of auctions
            if not isinstance(region_map, dict):
                continue

            for region_name, auctions_list in region_map.items():
                if not isinstance(auctions_list, list):
                    continue

                for entry in auctions_list:
                    base_auction = entry.get("base_auction", {})
                    auction_obj = self._create_auction_from_dict(
                        base_auction_dict=base_auction,
                        fallback_region_name=region_name,
                    )
                    if auction_obj is not None:
                        all_auctions.append(auction_obj)

        self._logger.info(
            "Loaded %d auctions total from catalog '%s'.",
            len(all_auctions),
            catalog_path,
        )
        return all_auctions

    def _create_auction_from_dict(
        self,
        *,
        base_auction_dict: Dict[str, Any],
        fallback_region_name: str,
    ) -> Optional[Auction]:
        """Creates an `Auction` from a `base_auction` entry in the local YAML.

        Args:
            base_auction_dict: Dictionary containing `'base_auction'` data.
            fallback_region_name: Region name gleaned from the YAML hierarchy
                if not present in the dict.

        Returns:
            An `Auction` object or None if there's an error.
        """
        try:
            mapped_data = {
                "cluster_id": base_auction_dict.get("id"),
                "gpu_type": base_auction_dict.get("gpu_type"),
                "inventory_quantity": base_auction_dict.get("inventory_quantity"),
                "num_gpu": base_auction_dict.get("num_gpu"),
                "intranode_interconnect": base_auction_dict.get(
                    "intranode_interconnect"
                ),
                "internode_interconnect": base_auction_dict.get(
                    "internode_interconnect"
                ),
                "fcp_instance": base_auction_dict.get("fcp_instance"),
                "instance_type_id": base_auction_dict.get("instance_type_id"),
                "last_price": base_auction_dict.get("last_price"),
                "region": base_auction_dict.get("region", fallback_region_name),
                "region_id": base_auction_dict.get("region_id"),
                "resource_specification_id": base_auction_dict.get(
                    "resource_specification_id"
                ),
            }
            auction_obj = Auction(**mapped_data)
            return auction_obj
        except Exception as exc:  # Could be KeyError, ValidationError, etc.
            self._logger.warning(
                "Failed to parse an auction in region '%s': %s",
                fallback_region_name,
                exc,
            )
            return None

    def _enrich_auctions_with_catalog_data(
        self,
        *,
        foundry_auctions: List[Auction],
        local_catalog_auctions: List[Auction],
    ) -> List[Auction]:
        """Enriches Foundry auctions with missing fields from a local catalog.

        Merges data by matching on the `instance_type_id`. For each Foundry auction,
        any missing fields are filled from the local catalog if a matching
        `instance_type_id` is found.

        Args:
            foundry_auctions: A list of auctions fetched from Foundry.
            local_catalog_auctions: A list of auctions from the static catalog.

        Returns:
            A list of auctions with enriched data where possible.
        """
        self._logger.debug("Enriching Foundry auctions with local catalog data...")

        local_by_instance_type: Dict[str, Auction] = {}
        for local_auction in local_catalog_auctions:
            if local_auction.instance_type_id:
                local_by_instance_type[local_auction.instance_type_id] = local_auction

        enriched_list: List[Auction] = []

        for foundry_auction in foundry_auctions:
            if not foundry_auction.instance_type_id:
                # If there's no instance_type_id, we can't match it. Keep as is.
                enriched_list.append(foundry_auction)
                continue

            local_match = local_by_instance_type.get(foundry_auction.instance_type_id)
            if not local_match:
                # No local data to enrich with
                enriched_list.append(foundry_auction)
                continue

            # Merge: fill missing Foundry fields with local data
            merged = Auction(
                cluster_id=(foundry_auction.cluster_id or local_match.cluster_id),
                gpu_type=foundry_auction.gpu_type or local_match.gpu_type,
                inventory_quantity=(
                    foundry_auction.inventory_quantity
                    if foundry_auction.inventory_quantity is not None
                    else local_match.inventory_quantity
                ),
                num_gpu=foundry_auction.num_gpus or local_match.num_gpus,
                intranode_interconnect=(
                    foundry_auction.intranode_interconnect
                    or local_match.intranode_interconnect
                ),
                internode_interconnect=(
                    foundry_auction.internode_interconnect
                    or local_match.internode_interconnect
                ),
                fcp_instance=(foundry_auction.fcp_instance or local_match.fcp_instance),
                instance_type_id=foundry_auction.instance_type_id,  # keep Foundry's
                last_price=foundry_auction.last_price or local_match.last_price,
                region=foundry_auction.region or local_match.region,
                region_id=foundry_auction.region_id or local_match.region_id,
                resource_specification_id=(
                    foundry_auction.resource_specification_id
                    or local_match.resource_specification_id
                ),
            )
            enriched_list.append(merged)

        self._logger.info(
            "Enriched %d Foundry auctions with local data. Returning %d total.",
            len(foundry_auctions),
            len(enriched_list),
        )
        return enriched_list
