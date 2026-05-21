"""
Pydantic models for the expose API.
"""

import re
import time
from datetime import datetime
from typing import List, Optional

import yaml
from pydantic import BaseModel, field_validator, model_validator

# Regex for valid expose names: lowercase alphanumeric, hyphens, underscores
# Must start with letter or number
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def slugify_summary(summary: Optional[str]) -> str:
    """
    Convert a summary string into a valid identifier slug.

    Rules:
    1. Use flow.summary as source
    2. Make it lowercase
    3. Replace whitespace and special characters with "-"
    4. Replace consecutive "--" with single "-"
    5. Strip leading/trailing hyphens

    Args:
        summary: Flow summary string or None

    Returns:
        A valid slug identifier, or "unnamed-flow" if summary is empty
    """
    if not summary:
        return "unnamed-flow"

    # Lowercase
    slug = summary.lower()

    # Replace whitespace and special characters with hyphen
    # Keep only alphanumeric and hyphens
    slug = re.sub(r"[^a-z0-9]+", "-", slug)

    # Replace consecutive hyphens with single hyphen
    slug = re.sub(r"-+", "-", slug)

    # Strip leading/trailing hyphens
    slug = slug.strip("-")

    # Ensure non-empty result
    return slug or "unnamed-flow"


class ExposeMeta(BaseModel):
    """Meta entry for a flow endpoint within an expose.

    The flow is identified by URL endpoint - the identifier is derived
    from the last segment of the URL path.
    """
    url: str
    data: Optional[str] = None
    enabled: bool = True  # Can disable individual flows
    state: Optional[str] = None  # read-only: alive|dead|not-found
    heartbeat: Optional[float] = None  # read-only: timestamp


class ExposeCreate(BaseModel):
    """Request model for creating/updating an expose item."""
    name: str
    original_name: Optional[str] = None  # For tracking renames
    display: Optional[str] = None
    network: Optional[str] = None
    enabled: bool = True
    bootstrap: Optional[str] = None
    meta: Optional[List[ExposeMeta]] = None
    etag: Optional[str] = None  # For optimistic concurrency control on updates

    @field_validator("name", "original_name", mode="before")
    @classmethod
    def normalize_name(cls, v: Optional[str]) -> Optional[str]:
        """Convert to lowercase and validate format."""
        if v:
            v = v.lower().strip()
        return v

    @field_validator("name", mode="after")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate name format."""
        if not NAME_PATTERN.match(v):
            raise ValueError(
                "Name must start with a letter or number and contain only "
                "lowercase letters, numbers, hyphens, and underscores"
            )
        return v

    @field_validator("bootstrap", mode="after")
    @classmethod
    def validate_bootstrap_yaml(cls, v: Optional[str]) -> Optional[str]:
        """Validate bootstrap is valid YAML (syntax only)."""
        if v is not None and v.strip():
            try:
                yaml.safe_load(v)
            except yaml.YAMLError as e:
                raise ValueError(f"Invalid YAML in bootstrap: {e}")
        return v

    @model_validator(mode="after")
    def validate_meta_data(self):
        """Validate meta entries have valid YAML in data field."""
        if self.meta:
            for entry in self.meta:
                if entry.data is not None and entry.data.strip():
                    try:
                        yaml.safe_load(entry.data)
                    except yaml.YAMLError as e:
                        raise ValueError(
                            f"Invalid YAML in meta data for {entry.url}: {e}"
                        )
        return self


class ExposeResponse(BaseModel):
    """Response model for an expose item."""
    name: str
    display: Optional[str] = None
    network: Optional[str] = None
    enabled: bool
    state: str
    heartbeat: Optional[float] = None
    bootstrap: Optional[str] = None
    meta: Optional[List[ExposeMeta]] = None
    created: datetime
    updated: datetime
    etag: str  # For optimistic concurrency control (based on updated timestamp)
    # Computed fields for UI display (not stored in database)
    flow_stats: str = "0/0"
    stale: bool = False
    needs_reboot: bool = False

    @classmethod
    def from_db_row(cls, row: dict) -> "ExposeResponse":
        """Create from database row."""
        meta = None
        if row.get("meta"):
            try:
                meta_list = yaml.safe_load(row["meta"])
                if meta_list:
                    meta = [ExposeMeta(**m) for m in meta_list]
            except (yaml.YAMLError, TypeError):
                pass

        updated_ts = row["updated"]
        return cls(
            name=row["name"],
            display=row.get("display"),
            network=row.get("network"),
            enabled=bool(row.get("enabled", True)),
            state=row.get("state", "DRAFT"),
            heartbeat=row.get("heartbeat"),
            bootstrap=row.get("bootstrap"),
            meta=meta,
            created=datetime.fromtimestamp(row["created"]),
            updated=datetime.fromtimestamp(updated_ts),
            etag=str(updated_ts),  # Use timestamp as ETag
        )


def meta_to_yaml(meta: Optional[List[ExposeMeta]]) -> Optional[str]:
    """Convert meta list to YAML string for storage."""
    if not meta:
        return None
    return yaml.dump(
        [m.model_dump(exclude_none=True) for m in meta],
        default_flow_style=False,
        allow_unicode=True
    )


def create_meta_template(
    url: str,
    summary: Optional[str] = None,
    description: Optional[str] = None,
    author: Optional[str] = None,
    organization: Optional[str] = None,
    tags: Optional[List[str]] = None
) -> ExposeMeta:
    """
    Create a meta entry template with commented data field.

    This is used when discovering flows from GET /flow to provide
    a starting template for devops to fill in.
    """
    data_template = f"""# Flow metadata configuration

# Display name for this flow (shown in UI)
display: {summary or 'Unnamed Flow'}

# Description of what this flow does
description: {description or 'No description provided'}

# Tags for categorization and search
tags:
{chr(10).join(f'  - {tag}' for tag in (tags or [])) or '  # - example-tag'}

# Author information
author:
  # Author name
  name: {author or '~'}
  # Organization providing this service
  organization: {organization or '~'}
  # Contact email for support/questions
  contact_email: ~
  # Optional: Other contact methods
  contact_other: ~

# Example inputs for documentation
# example:
#   - name: example_input
#     mime_type: application/json
#     url: https://example.com/sample.json

# Capability declaration
# capability:
#   name: my-capability
#   version: 1.0.0

# Legal information
# legal:
#   privacy_policy: https://example.com/privacy
#   terms: https://example.com/terms

# Pricing configuration
# agentPricing:
#   pricingType: fixed  # fixed | variable | free
#   fixedPricing:
#     - amount: 1000000  # in lovelace
#       unit: lovelace

# Preview image URL
# image: https://example.com/preview.png
"""
    return ExposeMeta(
        url=url,
        # name is deprecated - identifier derived from URL endpoint
        data=data_template,
        state=None,
        heartbeat=None
    )
