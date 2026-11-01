"""Initial SFP cross-repository relationship graph baseline.

This catalog is a one-time bootstrap input. The accepted proposal is stored in
``STATEFORGE_DPT.md`` and becomes the canonical graph; this module is not read by
the running DPT service.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


def _node(
    node_id: str,
    repository_alias: str,
    kind: str,
    label: str,
    path: str,
    symbol: str | None = None,
    *,
    parent_node_id: str | None = None,
    procedure_id: str | None = None,
    implementation_state: str | None = None,
) -> dict[str, Any]:
    if procedure_id is None:
        procedure_id = next(
            (
                procedure
                for fragment, procedure in (
                    ("endpoint", "endpoint-discovery"),
                    ("bootstrap", "bootstrap"),
                    ("capability", "capability-assessment"),
                    ("health", "health"),
                    ("manifest", "provider-manifest"),
                    ("operation_admission", "operation-admission"),
                    ("operation_lifecycle", "operation-lifecycle"),
                    ("trigger", "trigger-publication"),
                    ("recovery", "disconnection-recovery"),
                )
                if fragment in node_id
            ),
            "shared",
        )
    return {
        "node_id": node_id,
        "repository_alias": repository_alias,
        "kind": kind,
        "label": label,
        "path": path,
        "symbol": symbol,
        "parent_node_id": parent_node_id,
        "procedure_id": procedure_id,
        "implementation_state": (
            implementation_state
            or ("expected" if node_id.endswith(".expected") else "implemented")
        ),
    }


PROTOCOL_MODULES = (
    _node(
        "sfp.validation.core",
        "protocol",
        "python_module",
        "Shared bounded validation",
        "src/stateforge_protocol/sfp/_validation.py",
    ),
    _node(
        "sfp.primitives",
        "protocol",
        "python_module",
        "Protocol scalar primitives",
        "src/stateforge_protocol/sfp/primitives.py",
    ),
    _node(
        "sfp.shared_endpoint",
        "protocol",
        "python_module",
        "Shared endpoint transport contract",
        "src/stateforge_protocol/sfp/shared_endpoint.py",
    ),
    _node(
        "sfp.recovery",
        "protocol",
        "python_module",
        "Recovery classification contract",
        "src/stateforge_protocol/sfp/recovery.py",
    ),
    _node(
        "sfp.endpoint_discovery.contract",
        "protocol",
        "python_module",
        "Endpoint discovery contract",
        "src/stateforge_protocol/sfp/endpoint_discovery/v1/contract.py",
    ),
    _node(
        "sfp.bootstrap.contract",
        "protocol",
        "python_module",
        "Bootstrap public contract",
        "src/stateforge_protocol/sfp/bootstrap/v1/contract.py",
    ),
    _node(
        "sfp.bootstrap.validation",
        "protocol",
        "python_module",
        "Bootstrap validation",
        "src/stateforge_protocol/sfp/bootstrap/v1/validation.py",
    ),
    _node(
        "sfp.capability.contract",
        "protocol",
        "python_module",
        "Capability Assessment public contract",
        "src/stateforge_protocol/sfp/capability_assessment/v1/contract.py",
    ),
    _node(
        "sfp.capability.validation",
        "protocol",
        "python_module",
        "Capability Assessment validation",
        "src/stateforge_protocol/sfp/capability_assessment/v1/validation.py",
    ),
    _node(
        "sfp.health.contract",
        "protocol",
        "python_module",
        "Health procedure contract",
        "src/stateforge_protocol/sfp/health_contract.py",
    ),
    _node(
        "sfp.manifest.contract",
        "protocol",
        "python_module",
        "Manifest public contract",
        "src/stateforge_protocol/sfp/manifest/v1/contract.py",
    ),
    _node(
        "sfp.manifest.presentation",
        "protocol",
        "python_module",
        "Manifest presentation validation",
        "src/stateforge_protocol/sfp/manifest/v1/_presentation.py",
    ),
    _node(
        "sfp.manifest.schema_inspection",
        "protocol",
        "python_module",
        "Manifest schema inspection",
        "src/stateforge_protocol/sfp/manifest/v1/_schema_inspection.py",
    ),
    _node(
        "sfp.manifest.validation",
        "protocol",
        "python_module",
        "Manifest validation",
        "src/stateforge_protocol/sfp/manifest/v1/validation.py",
    ),
    _node(
        "sfp.operation_admission.contract",
        "protocol",
        "python_module",
        "Operation Admission public contract",
        "src/stateforge_protocol/sfp/operation_admission/v1/contract.py",
    ),
    _node(
        "sfp.operation_admission.validation",
        "protocol",
        "python_module",
        "Operation Admission validation",
        "src/stateforge_protocol/sfp/operation_admission/v1/validation.py",
    ),
    _node(
        "sfp.operation_lifecycle.contract",
        "protocol",
        "python_module",
        "Operation Lifecycle public contract",
        "src/stateforge_protocol/sfp/operation_lifecycle/v1/contract.py",
    ),
    _node(
        "sfp.operation_lifecycle.validation",
        "protocol",
        "python_module",
        "Operation Lifecycle validation",
        "src/stateforge_protocol/sfp/operation_lifecycle/v1/validation.py",
    ),
    _node(
        "sfp.trigger_publication.contract",
        "protocol",
        "python_module",
        "Trigger Publication public contract",
        "src/stateforge_protocol/sfp/trigger_publication/v1/contract.py",
    ),
    _node(
        "sfp.trigger_publication.validation",
        "protocol",
        "python_module",
        "Trigger Publication validation",
        "src/stateforge_protocol/sfp/trigger_publication/v1/validation.py",
    ),
)


PROTOCOL_SERVICES = (
    _node(
        "sfp.bootstrap.service",
        "protocol",
        "service",
        "BootstrapService",
        "proto/stateforge_protocol/sfp/bootstrap/v1/bootstrap.proto",
        "stateforge.sfp.bootstrap.v1.BootstrapService",
    ),
    _node(
        "sfp.capability.service",
        "protocol",
        "service",
        "CapabilityAssessmentService",
        "proto/stateforge_protocol/sfp/capability_assessment/v1/capability_assessment.proto",
        "stateforge.sfp.capability_assessment.v1.CapabilityAssessmentService",
    ),
    _node(
        "sfp.manifest.service",
        "protocol",
        "service",
        "ManifestService",
        "proto/stateforge_protocol/sfp/manifest/v1/manifest.proto",
        "stateforge.sfp.manifest.v1.ManifestService",
    ),
    _node(
        "sfp.operation_admission.service",
        "protocol",
        "service",
        "OperationAdmissionService",
        "proto/stateforge_protocol/sfp/operation_admission/v1/operation_admission.proto",
        "stateforge.sfp.operation_admission.v1.OperationAdmissionService",
    ),
    _node(
        "sfp.operation_lifecycle.service",
        "protocol",
        "service",
        "OperationLifecycleService",
        "proto/stateforge_protocol/sfp/operation_lifecycle/v1/operation_lifecycle.proto",
        "stateforge.sfp.operation_lifecycle.v1.OperationLifecycleService",
    ),
    _node(
        "sfp.trigger_publication.service",
        "protocol",
        "service",
        "TriggerPublicationService",
        "proto/stateforge_protocol/sfp/trigger_publication/v1/trigger_publication.proto",
        "stateforge.sfp.trigger_publication.v1.TriggerPublicationService",
    ),
)


def _proto_child(
    procedure: str,
    name: str,
    kind: str,
    path: str,
    package: str,
) -> dict[str, Any]:
    """Create one RPC/message child under its protocol service."""
    service_id = f"sfp.{procedure}.service"
    return _node(
        f"sfp.{procedure}.{kind}.{name}",
        "protocol",
        kind,
        name,
        path,
        f"{package}.{name}",
        parent_node_id=service_id,
    )


PROTOCOL_RPC_AND_MESSAGES = (
    _proto_child(
        "bootstrap",
        "Handshake",
        "rpc",
        "proto/stateforge_protocol/sfp/bootstrap/v1/bootstrap.proto",
        "stateforge.sfp.bootstrap.v1.BootstrapService",
    ),
    _proto_child(
        "bootstrap",
        "HandshakeRequest",
        "message",
        "proto/stateforge_protocol/sfp/bootstrap/v1/bootstrap.proto",
        "stateforge.sfp.bootstrap.v1",
    ),
    _proto_child(
        "bootstrap",
        "HandshakeResponse",
        "message",
        "proto/stateforge_protocol/sfp/bootstrap/v1/bootstrap.proto",
        "stateforge.sfp.bootstrap.v1",
    ),
    _proto_child(
        "capability",
        "AssessCapabilities",
        "rpc",
        "proto/stateforge_protocol/sfp/capability_assessment/v1/capability_assessment.proto",
        "stateforge.sfp.capability_assessment.v1.CapabilityAssessmentService",
    ),
    _proto_child(
        "capability",
        "AssessCapabilitiesRequest",
        "message",
        "proto/stateforge_protocol/sfp/capability_assessment/v1/capability_assessment.proto",
        "stateforge.sfp.capability_assessment.v1",
    ),
    _proto_child(
        "capability",
        "AssessCapabilitiesResponse",
        "message",
        "proto/stateforge_protocol/sfp/capability_assessment/v1/capability_assessment.proto",
        "stateforge.sfp.capability_assessment.v1",
    ),
    _proto_child(
        "manifest",
        "GetManifest",
        "rpc",
        "proto/stateforge_protocol/sfp/manifest/v1/manifest.proto",
        "stateforge.sfp.manifest.v1.ManifestService",
    ),
    _proto_child(
        "manifest",
        "GetManifestRequest",
        "message",
        "proto/stateforge_protocol/sfp/manifest/v1/manifest.proto",
        "stateforge.sfp.manifest.v1",
    ),
    _proto_child(
        "manifest",
        "GetManifestResponse",
        "message",
        "proto/stateforge_protocol/sfp/manifest/v1/manifest.proto",
        "stateforge.sfp.manifest.v1",
    ),
    _proto_child(
        "operation_admission",
        "StartOperation",
        "rpc",
        "proto/stateforge_protocol/sfp/operation_admission/v1/operation_admission.proto",
        "stateforge.sfp.operation_admission.v1.OperationAdmissionService",
    ),
    _proto_child(
        "operation_admission",
        "StartOperationRequest",
        "message",
        "proto/stateforge_protocol/sfp/operation_admission/v1/operation_admission.proto",
        "stateforge.sfp.operation_admission.v1",
    ),
    _proto_child(
        "operation_admission",
        "StartOperationResponse",
        "message",
        "proto/stateforge_protocol/sfp/operation_admission/v1/operation_admission.proto",
        "stateforge.sfp.operation_admission.v1",
    ),
    _proto_child(
        "operation_lifecycle",
        "WatchOperation",
        "rpc",
        "proto/stateforge_protocol/sfp/operation_lifecycle/v1/operation_lifecycle.proto",
        "stateforge.sfp.operation_lifecycle.v1.OperationLifecycleService",
    ),
    _proto_child(
        "operation_lifecycle",
        "CancelOperation",
        "rpc",
        "proto/stateforge_protocol/sfp/operation_lifecycle/v1/operation_lifecycle.proto",
        "stateforge.sfp.operation_lifecycle.v1.OperationLifecycleService",
    ),
    _proto_child(
        "operation_lifecycle",
        "WatchOperationRequest",
        "message",
        "proto/stateforge_protocol/sfp/operation_lifecycle/v1/operation_lifecycle.proto",
        "stateforge.sfp.operation_lifecycle.v1",
    ),
    _proto_child(
        "operation_lifecycle",
        "WatchOperationResponse",
        "message",
        "proto/stateforge_protocol/sfp/operation_lifecycle/v1/operation_lifecycle.proto",
        "stateforge.sfp.operation_lifecycle.v1",
    ),
    _proto_child(
        "operation_lifecycle",
        "CancelOperationRequest",
        "message",
        "proto/stateforge_protocol/sfp/operation_lifecycle/v1/operation_lifecycle.proto",
        "stateforge.sfp.operation_lifecycle.v1",
    ),
    _proto_child(
        "operation_lifecycle",
        "CancelOperationResponse",
        "message",
        "proto/stateforge_protocol/sfp/operation_lifecycle/v1/operation_lifecycle.proto",
        "stateforge.sfp.operation_lifecycle.v1",
    ),
    _proto_child(
        "trigger_publication",
        "StreamTriggers",
        "rpc",
        "proto/stateforge_protocol/sfp/trigger_publication/v1/trigger_publication.proto",
        "stateforge.sfp.trigger_publication.v1.TriggerPublicationService",
    ),
    _proto_child(
        "trigger_publication",
        "StreamTriggersRequest",
        "message",
        "proto/stateforge_protocol/sfp/trigger_publication/v1/trigger_publication.proto",
        "stateforge.sfp.trigger_publication.v1",
    ),
    _proto_child(
        "trigger_publication",
        "StreamTriggersResponse",
        "message",
        "proto/stateforge_protocol/sfp/trigger_publication/v1/trigger_publication.proto",
        "stateforge.sfp.trigger_publication.v1",
    ),
)


IMPLEMENTATION_NODES = (
    _node(
        "provider.endpoint.descriptor",
        "provider",
        "class",
        "Provider EndpointDescriptor",
        "src/stateforge_provider/_runtime/descriptor.py",
        "EndpointDescriptor",
    ),
    _node(
        "provider.identity.store",
        "provider",
        "class",
        "ProviderIdentityStore",
        "src/stateforge_provider/_runtime/identity.py",
        "ProviderIdentityStore",
    ),
    _node(
        "provider.session.registry",
        "provider",
        "class",
        "Provider SessionRegistry",
        "src/stateforge_provider/_runtime/sessions.py",
        "SessionRegistry",
    ),
    _node(
        "provider.bootstrap.service",
        "provider",
        "class",
        "Provider BootstrapService",
        "src/stateforge_provider/_services/bootstrap/service.py",
        "BootstrapService",
    ),
    _node(
        "provider.capability.declaration",
        "provider",
        "class",
        "RequirementRegistry",
        "src/stateforge_provider/_services/capability_assessment/declaration.py",
        "RequirementRegistry",
    ),
    _node(
        "provider.capability.service",
        "provider",
        "class",
        "Provider CapabilityAssessmentService",
        "src/stateforge_provider/_services/capability_assessment/service.py",
        "CapabilityAssessmentService",
    ),
    _node(
        "provider.health.service",
        "provider",
        "class",
        "ProviderHealthService",
        "src/stateforge_provider/_services/health/service.py",
        "ProviderHealthService",
    ),
    _node(
        "provider.manifest.declaration",
        "provider",
        "class",
        "ManifestDeclaration",
        "src/stateforge_provider/_services/manifest/declaration.py",
        "ManifestDeclaration",
    ),
    _node(
        "provider.manifest.service",
        "provider",
        "class",
        "Provider ManifestService",
        "src/stateforge_provider/_services/manifest/service.py",
        "ManifestService",
    ),
    _node(
        "provider.trigger.runtime",
        "provider",
        "class",
        "TriggerPublicationRuntime",
        "src/stateforge_provider/_services/trigger_publication/runtime.py",
        "TriggerPublicationRuntime",
    ),
    _node(
        "provider.trigger.service",
        "provider",
        "class",
        "Provider TriggerPublicationService",
        "src/stateforge_provider/_services/trigger_publication/service.py",
        "TriggerPublicationService",
    ),
    _node(
        "provider.operation_admission.expected",
        "provider",
        "class",
        "Expected OperationAdmissionService",
        "src/stateforge_provider/_services/operation_admission/service.py",
        "OperationAdmissionService",
    ),
    _node(
        "provider.operation_lifecycle.expected",
        "provider",
        "class",
        "Expected OperationLifecycleService",
        "src/stateforge_provider/_services/operation_lifecycle/service.py",
        "OperationLifecycleService",
    ),
    _node(
        "agent.endpoint.descriptor",
        "agent",
        "class",
        "ProviderEndpointDescriptor",
        "src/stateforge_agent/_sfp/_runtime/descriptor.py",
        "ProviderEndpointDescriptor",
    ),
    _node(
        "agent.channel",
        "agent",
        "function",
        "create_provider_channel",
        "src/stateforge_agent/_sfp/_runtime/channel.py",
        "create_provider_channel",
    ),
    _node(
        "agent.session.manager",
        "agent",
        "class",
        "ProviderSessionManager",
        "src/stateforge_agent/_sfp/_runtime/sessions.py",
        "ProviderSessionManager",
    ),
    _node(
        "agent.bootstrap.client",
        "agent",
        "function",
        "perform_handshake",
        "src/stateforge_agent/_sfp/_services/bootstrap/client.py",
        "perform_handshake",
    ),
    _node(
        "agent.capability.client",
        "agent",
        "class",
        "CapabilityAssessmentClient",
        "src/stateforge_agent/_sfp/_services/capability_assessment/client.py",
        "CapabilityAssessmentClient",
    ),
    _node(
        "agent.capability.coordinator",
        "agent",
        "class",
        "CapabilityAssessmentCoordinator",
        "src/stateforge_agent/_sfp/_services/capability_assessment/coordinator.py",
        "CapabilityAssessmentCoordinator",
    ),
    _node(
        "agent.health.client",
        "agent",
        "class",
        "HealthClient",
        "src/stateforge_agent/_sfp/_services/health/client.py",
        "HealthClient",
    ),
    _node(
        "agent.manifest.declaration",
        "agent",
        "class",
        "Agent ManifestDeclaration",
        "src/stateforge_agent/_sfp/_services/manifest/declaration.py",
        "ManifestDeclaration",
    ),
    _node(
        "agent.manifest.client",
        "agent",
        "class",
        "ManifestClient",
        "src/stateforge_agent/_sfp/_services/manifest/client.py",
        "ManifestClient",
    ),
    _node(
        "agent.trigger.client",
        "agent",
        "class",
        "TriggerPublicationClient",
        "src/stateforge_agent/_sfp/_services/trigger_publication/client.py",
        "TriggerPublicationClient",
    ),
    _node(
        "agent.trigger.coordinator",
        "agent",
        "class",
        "TriggerPublicationCoordinator",
        "src/stateforge_agent/_sfp/_services/trigger_publication/coordinator.py",
        "TriggerPublicationCoordinator",
    ),
    _node(
        "agent.trigger.store",
        "agent",
        "storage_contract",
        "TriggerPublicationStore",
        "src/stateforge_agent/_sfp/_services/trigger_publication/store.py",
        "TriggerPublicationStore",
    ),
    _node(
        "agent.trigger.sqlite",
        "agent",
        "storage_implementation",
        "SqliteTriggerPublicationStore",
        "src/stateforge_agent/_journal/sqlite_trigger_publications.py",
        "SqliteTriggerPublicationStore",
    ),
    _node(
        "agent.operation_admission.expected",
        "agent",
        "class",
        "Expected OperationAdmissionClient",
        "src/stateforge_agent/_sfp/_services/operation_admission/client.py",
        "OperationAdmissionClient",
    ),
    _node(
        "agent.operation_lifecycle.expected",
        "agent",
        "class",
        "Expected OperationLifecycleClient",
        "src/stateforge_agent/_sfp/_services/operation_lifecycle/client.py",
        "OperationLifecycleClient",
    ),
)


IMPLEMENTATION_METHOD_NODES = (
    _node(
        "provider.bootstrap.rpc.Handshake",
        "provider",
        "function",
        "BootstrapService.Handshake",
        "src/stateforge_provider/_services/bootstrap/service.py",
        "BootstrapService.Handshake",
        parent_node_id="provider.bootstrap.service",
    ),
    _node(
        "provider.capability.rpc.AssessCapabilities",
        "provider",
        "function",
        "CapabilityAssessmentService.AssessCapabilities",
        "src/stateforge_provider/_services/capability_assessment/service.py",
        "CapabilityAssessmentService.AssessCapabilities",
        parent_node_id="provider.capability.service",
    ),
    _node(
        "provider.manifest.rpc.GetManifest",
        "provider",
        "function",
        "ManifestService.GetManifest",
        "src/stateforge_provider/_services/manifest/service.py",
        "ManifestService.GetManifest",
        parent_node_id="provider.manifest.service",
    ),
    _node(
        "provider.trigger_publication.rpc.StreamTriggers",
        "provider",
        "function",
        "TriggerPublicationService.StreamTriggers",
        "src/stateforge_provider/_services/trigger_publication/service.py",
        "TriggerPublicationService.StreamTriggers",
        parent_node_id="provider.trigger.service",
    ),
    _node(
        "provider.operation_admission.rpc.StartOperation",
        "provider",
        "function",
        "OperationAdmissionService.StartOperation",
        "src/stateforge_provider/_services/operation_admission/service.py",
        "OperationAdmissionService.StartOperation",
        parent_node_id="provider.operation_admission.expected",
        implementation_state="expected",
    ),
    _node(
        "provider.operation_lifecycle.rpc.WatchOperation",
        "provider",
        "function",
        "OperationLifecycleService.WatchOperation",
        "src/stateforge_provider/_services/operation_lifecycle/service.py",
        "OperationLifecycleService.WatchOperation",
        parent_node_id="provider.operation_lifecycle.expected",
        implementation_state="expected",
    ),
    _node(
        "provider.operation_lifecycle.rpc.CancelOperation",
        "provider",
        "function",
        "OperationLifecycleService.CancelOperation",
        "src/stateforge_provider/_services/operation_lifecycle/service.py",
        "OperationLifecycleService.CancelOperation",
        parent_node_id="provider.operation_lifecycle.expected",
        implementation_state="expected",
    ),
    _node(
        "agent.capability.rpc.assess",
        "agent",
        "function",
        "CapabilityAssessmentClient.assess",
        "src/stateforge_agent/_sfp/_services/capability_assessment/client.py",
        "CapabilityAssessmentClient.assess",
        parent_node_id="agent.capability.client",
    ),
    _node(
        "agent.manifest.rpc.retrieve",
        "agent",
        "function",
        "ManifestClient.retrieve",
        "src/stateforge_agent/_sfp/_services/manifest/client.py",
        "ManifestClient.retrieve",
        parent_node_id="agent.manifest.client",
    ),
    _node(
        "agent.trigger_publication.rpc.consume",
        "agent",
        "function",
        "TriggerPublicationClient.consume",
        "src/stateforge_agent/_sfp/_services/trigger_publication/client.py",
        "TriggerPublicationClient.consume",
        parent_node_id="agent.trigger.client",
    ),
    _node(
        "agent.operation_admission.rpc.start",
        "agent",
        "function",
        "OperationAdmissionClient.start",
        "src/stateforge_agent/_sfp/_services/operation_admission/client.py",
        "OperationAdmissionClient.start",
        parent_node_id="agent.operation_admission.expected",
        implementation_state="expected",
    ),
    _node(
        "agent.operation_lifecycle.rpc.watch",
        "agent",
        "function",
        "OperationLifecycleClient.watch",
        "src/stateforge_agent/_sfp/_services/operation_lifecycle/client.py",
        "OperationLifecycleClient.watch",
        parent_node_id="agent.operation_lifecycle.expected",
        implementation_state="expected",
    ),
    _node(
        "agent.operation_lifecycle.rpc.cancel",
        "agent",
        "function",
        "OperationLifecycleClient.cancel",
        "src/stateforge_agent/_sfp/_services/operation_lifecycle/client.py",
        "OperationLifecycleClient.cancel",
        parent_node_id="agent.operation_lifecycle.expected",
        implementation_state="expected",
    ),
)


RPC_COUNTERPARTS: dict[str, tuple[str, ...]] = {
    "sfp.bootstrap.rpc.Handshake": (
        "provider.bootstrap.rpc.Handshake",
        "agent.bootstrap.client",
    ),
    "sfp.capability.rpc.AssessCapabilities": (
        "provider.capability.rpc.AssessCapabilities",
        "agent.capability.rpc.assess",
    ),
    "sfp.manifest.rpc.GetManifest": (
        "provider.manifest.rpc.GetManifest",
        "agent.manifest.rpc.retrieve",
    ),
    "sfp.operation_admission.rpc.StartOperation": (
        "provider.operation_admission.rpc.StartOperation",
        "agent.operation_admission.rpc.start",
    ),
    "sfp.operation_lifecycle.rpc.WatchOperation": (
        "provider.operation_lifecycle.rpc.WatchOperation",
        "agent.operation_lifecycle.rpc.watch",
    ),
    "sfp.operation_lifecycle.rpc.CancelOperation": (
        "provider.operation_lifecycle.rpc.CancelOperation",
        "agent.operation_lifecycle.rpc.cancel",
    ),
    "sfp.trigger_publication.rpc.StreamTriggers": (
        "provider.trigger_publication.rpc.StreamTriggers",
        "agent.trigger_publication.rpc.consume",
    ),
}


_GROUP_TARGETS: dict[str, tuple[str, ...]] = {
    "sfp.validation.core": (
        "provider.manifest.declaration",
        "agent.manifest.declaration",
    ),
    "sfp.primitives": ("provider.identity.store", "agent.trigger.coordinator"),
    "sfp.shared_endpoint": ("agent.channel",),
    "sfp.recovery": ("provider.session.registry", "agent.session.manager"),
    "sfp.endpoint_discovery.contract": (
        "provider.endpoint.descriptor",
        "agent.endpoint.descriptor",
    ),
    "sfp.bootstrap.contract": (
        "provider.bootstrap.service",
        "agent.bootstrap.client",
    ),
    "sfp.bootstrap.validation": (
        "provider.bootstrap.service",
        "agent.bootstrap.client",
    ),
    "sfp.capability.contract": (
        "provider.capability.declaration",
        "agent.capability.coordinator",
    ),
    "sfp.capability.validation": (
        "provider.capability.service",
        "agent.capability.client",
    ),
    "sfp.health.contract": ("provider.health.service", "agent.health.client"),
    "sfp.manifest.contract": (
        "provider.manifest.declaration",
        "agent.manifest.client",
    ),
    "sfp.manifest.presentation": (
        "provider.manifest.declaration",
        "agent.manifest.declaration",
    ),
    "sfp.manifest.schema_inspection": (
        "provider.manifest.declaration",
        "agent.manifest.declaration",
    ),
    "sfp.manifest.validation": (
        "provider.manifest.declaration",
        "agent.manifest.declaration",
    ),
    "sfp.operation_admission.contract": (
        "provider.operation_admission.expected",
        "agent.operation_admission.expected",
    ),
    "sfp.operation_admission.validation": (
        "provider.operation_admission.expected",
        "agent.operation_admission.expected",
    ),
    "sfp.operation_lifecycle.contract": (
        "provider.operation_lifecycle.expected",
        "agent.operation_lifecycle.expected",
    ),
    "sfp.operation_lifecycle.validation": (
        "provider.operation_lifecycle.expected",
        "agent.operation_lifecycle.expected",
    ),
    "sfp.trigger_publication.contract": (
        "provider.trigger.runtime",
        "agent.trigger.coordinator",
    ),
    "sfp.trigger_publication.validation": (
        "provider.trigger.runtime",
        "agent.trigger.coordinator",
    ),
    "sfp.bootstrap.service": (
        "provider.bootstrap.service",
        "agent.bootstrap.client",
    ),
    "sfp.capability.service": (
        "provider.capability.service",
        "agent.capability.client",
    ),
    "sfp.manifest.service": (
        "provider.manifest.service",
        "agent.manifest.client",
    ),
    "sfp.operation_admission.service": (
        "provider.operation_admission.expected",
        "agent.operation_admission.expected",
    ),
    "sfp.operation_lifecycle.service": (
        "provider.operation_lifecycle.expected",
        "agent.operation_lifecycle.expected",
    ),
    "sfp.trigger_publication.service": (
        "provider.trigger.service",
        "agent.trigger.client",
    ),
}


def _relationships(
    nodes: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {str(node["node_id"]): node for node in nodes}
    relationships: list[dict[str, Any]] = []
    for protocol_id, consumer_ids in _GROUP_TARGETS.items():
        for consumer_id in consumer_ids:
            consumer = by_id[consumer_id]
            missing = consumer_id.endswith(".expected")
            relationship_type = (
                "implements"
                if protocol_id.endswith(".service")
                and consumer["repository_alias"] == "provider"
                else "consumes"
            )
            relationships.append(
                {
                    "relationship_id": (
                        f"{consumer_id}.{relationship_type}.{protocol_id}"
                    ),
                    "source_node_id": consumer_id,
                    "target_node_id": protocol_id,
                    "relationship_type": relationship_type,
                    "directness": "conceptual",
                    "introduced_by_change_set_id": None,
                    "verification": {
                        "state": "declared",
                        "method": "manual",
                        "details": (
                            "Expected counterpart is not implemented yet."
                            if missing
                            else (
                                "Architectural ownership link in the initial "
                                "SFP baseline."
                            )
                        ),
                    },
                }
            )
    for protocol_id, counterpart_ids in RPC_COUNTERPARTS.items():
        for counterpart_id in counterpart_ids:
            counterpart = by_id[counterpart_id]
            missing = counterpart["implementation_state"] == "expected"
            relationship_type = (
                "implements"
                if counterpart["repository_alias"] == "provider"
                else "consumes"
            )
            relationships.append(
                {
                    "relationship_id": (
                        f"{counterpart_id}.{relationship_type}.{protocol_id}"
                    ),
                    "source_node_id": counterpart_id,
                    "target_node_id": protocol_id,
                    "relationship_type": relationship_type,
                    "directness": "direct",
                    "introduced_by_change_set_id": None,
                    "verification": {
                        "state": "declared",
                        "method": (
                            "manual"
                            if missing
                            else (
                                "python_ast"
                                if counterpart["repository_alias"] == "provider"
                                else "callsite"
                            )
                        ),
                        "details": (
                            "Expected RPC counterpart is not implemented yet."
                            if missing
                            else "Direct RPC implementation or call site."
                        ),
                    },
                }
            )
    return relationships


def sfp_baseline_operations(
    repository_shas: Mapping[str, str],
    *,
    tracking_started_at: str,
) -> list[dict[str, Any]]:
    """Return typed operations for the initial, human-reviewed SFP baseline."""
    aliases = ("protocol", "provider", "agent")
    missing = [alias for alias in aliases if alias not in repository_shas]
    if missing:
        raise ValueError("Missing repository revisions: " + ", ".join(missing))
    nodes = [
        *PROTOCOL_MODULES,
        *PROTOCOL_SERVICES,
        *PROTOCOL_RPC_AND_MESSAGES,
        *IMPLEMENTATION_NODES,
        *IMPLEMENTATION_METHOD_NODES,
    ]
    operations: list[dict[str, Any]] = [
        {
            "kind": "initialize_graph_tracking",
            "tracking_started_at": tracking_started_at,
        }
    ]
    operations.extend(
        {
            "kind": "set_graph_repository_snapshot",
            "snapshot": {
                "repository_alias": alias,
                "commit_sha": repository_shas[alias],
            },
        }
        for alias in aliases
    )
    operations.extend({"kind": "upsert_graph_node", "node": node} for node in nodes)
    operations.extend(
        {"kind": "upsert_graph_relationship", "relationship": relationship}
        for relationship in _relationships(nodes)
    )
    return operations


def unmapped_protocol_modules(protocol_root: Path) -> list[str]:
    """Return handwritten SFP modules absent from the bootstrap catalog."""
    source_root = protocol_root / "src/stateforge_protocol/sfp"
    discovered = {
        path.relative_to(protocol_root).as_posix()
        for path in source_root.rglob("*.py")
        if path.name != "__init__.py"
        and not path.name.endswith("_pb2.py")
        and not path.name.endswith("_pb2_grpc.py")
    }
    catalogued = {str(node["path"]) for node in PROTOCOL_MODULES}
    return sorted(discovered - catalogued)


def unmapped_protocol_paths(paths: Iterable[str]) -> list[str]:
    """Return handwritten SFP paths absent from the bootstrap catalog."""
    prefix = "src/stateforge_protocol/sfp/"
    discovered = {
        path
        for path in paths
        if path.startswith(prefix)
        and path.endswith(".py")
        and not path.endswith("/__init__.py")
        and not path.endswith("_pb2.py")
        and not path.endswith("_pb2_grpc.py")
    }
    catalogued = {str(node["path"]) for node in PROTOCOL_MODULES}
    return sorted(discovered - catalogued)
