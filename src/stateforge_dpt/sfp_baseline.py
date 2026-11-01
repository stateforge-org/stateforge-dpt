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
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "repository_alias": repository_alias,
        "kind": kind,
        "label": label,
        "path": path,
        "symbol": symbol,
        "parent_node_id": None,
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
                    "introduced_by_change_set_id": None,
                    "verification": {
                        "state": "missing" if missing else "declared",
                        "method": "manual" if missing else "import",
                        "details": (
                            "Expected counterpart is not implemented yet."
                            if missing
                            else "Initial SFP baseline; exact symbols are verified "
                            "at pinned repository revisions."
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
    nodes = [*PROTOCOL_MODULES, *PROTOCOL_SERVICES, *IMPLEMENTATION_NODES]
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

