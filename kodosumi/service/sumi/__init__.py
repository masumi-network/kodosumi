"""
Sumi Protocol - Masumi-compatible API (MIP-002/MIP-003/MIP-004).

This module provides endpoints for external systems to discover,
invoke, and interact with Kodosumi agent services.
"""

from kodosumi.service.sumi.models import (
    AuthorInfo,
    CapabilityInfo,
    LegalInfo,
    FixedPricing,
    AgentPricing,
    ExampleOutput,
    SumiFlowItem,
    SumiFlowListResponse,
    SumiServiceDetail,
    AvailabilityResponse,
    InputField,
    InputSchemaResponse,
    StartJobRequest,
    StartJobResponse,
    JobStatusResponse,
    PaymentInfo,
    LockSchemaResponse,
    ProvideInputRequest,
    ProvideInputResponse,
    ErrorResponse,
)
from kodosumi.service.sumi.hash import create_input_hash
from kodosumi.service.sumi.control import SumiControl, SumiLockControl

__all__ = [
    # MIP-002 Models
    "AuthorInfo",
    "CapabilityInfo",
    "LegalInfo",
    "FixedPricing",
    "AgentPricing",
    "ExampleOutput",
    # Discovery Models
    "SumiFlowItem",
    "SumiFlowListResponse",
    "SumiServiceDetail",
    # MIP-003 Models
    "AvailabilityResponse",
    "InputField",
    "InputSchemaResponse",
    "StartJobRequest",
    "StartJobResponse",
    "JobStatusResponse",
    "PaymentInfo",
    "LockSchemaResponse",
    "ProvideInputRequest",
    "ProvideInputResponse",
    "ErrorResponse",
    # MIP-004
    "create_input_hash",
    # Controllers
    "SumiControl",
    "SumiLockControl",
]
