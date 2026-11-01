import {
  ArrowRight,
  CheckCircle2,
  GitMerge,
  Network,
  Plus,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";

import { api } from "./api";
import { Markdown } from "./Markdown";
import type {
  Dashboard,
  PendingGraphRedeployment,
  PlanChangeProposal,
  RelationshipGraph,
  RelationshipGraphNode,
  RelationshipGraphRelationship,
} from "./types";
import { EmptyState, ErrorBanner, Loading, Modal, PageHeader } from "./ui";

const nodeKinds: RelationshipGraphNode["kind"][] = [
  "protobuf_package",
  "service",
  "rpc",
  "message",
  "python_module",
  "class",
  "function",
  "storage_contract",
  "storage_implementation",
  "test",
];

const relationshipTypes: RelationshipGraphRelationship["relationship_type"][] = [
  "implements",
  "consumes",
  "generated_from",
  "validates",
  "persists",
  "translates",
  "exposes",
  "tests",
];

type NodeDraft = RelationshipGraphNode;
type RelationshipDraft = RelationshipGraphRelationship;

function emptyNode(repositoryAlias: string): NodeDraft {
  return {
    node_id: "",
    repository_alias: repositoryAlias,
    kind: "function",
    label: "",
    path: "",
    symbol: null,
    parent_node_id: null,
  };
}

function emptyRelationship(changeSetId: string): RelationshipDraft {
  return {
    relationship_id: "",
    source_node_id: "",
    target_node_id: "",
    relationship_type: "consumes",
    introduced_by_change_set_id: changeSetId,
    verification: {
      state: "declared",
      method: "manual",
      details: "",
    },
  };
}

export function RelationshipGraphPage({
  dashboard,
  reviewer,
  onChanged,
}: {
  dashboard: Dashboard;
  reviewer: string;
  onChanged: () => Promise<void>;
}) {
  const [graph, setGraph] = useState<RelationshipGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<PendingGraphRedeployment | null>(null);
  const [disposition, setDisposition] = useState<
    "graph_updated" | "no_cross_repository_impact"
  >("graph_updated");
  const [reason, setReason] = useState("");
  const [nodes, setNodes] = useState<NodeDraft[]>([]);
  const [relationships, setRelationships] = useState<RelationshipDraft[]>([]);
  const [removeNodeIds, setRemoveNodeIds] = useState<string[]>([]);
  const [removeRelationshipIds, setRemoveRelationshipIds] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [proposal, setProposal] = useState<PlanChangeProposal | null>(null);
  const [verification, setVerification] = useState<null | {
    verification_status: string;
    failures: Array<{ node_id?: string; relationship_id?: string; detail: string }>;
  }>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setGraph(
        await api<RelationshipGraph>(
          `/api/projects/${dashboard.project.project_id}/relationship-graph`,
        ),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The graph could not be loaded");
    } finally {
      setLoading(false);
    }
  }

  async function verify() {
    setLoading(true);
    setError(null);
    try {
      setVerification(
        await api<{
          verification_status: string;
          failures: Array<{
            node_id?: string;
            relationship_id?: string;
            detail: string;
          }>;
        }>(
          `/api/projects/${dashboard.project.project_id}/relationship-graph/verify`,
          { method: "POST" },
        ),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Graph verification failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [dashboard.project.project_id, dashboard.project.working_plan_version]);

  const nodeById = useMemo(
    () => new Map((graph?.nodes || []).map((node) => [node.node_id, node])),
    [graph],
  );
  const nodesByRepository = useMemo(() => {
    const grouped = new Map<string, RelationshipGraphNode[]>();
    for (const node of graph?.nodes || []) {
      const values = grouped.get(node.repository_alias) || [];
      values.push(node);
      grouped.set(node.repository_alias, values);
    }
    return grouped;
  }, [graph]);

  function beginReview(item: PendingGraphRedeployment) {
    setSelected(item);
    setDisposition("graph_updated");
    setReason("");
    setNodes([]);
    setRelationships([]);
    setRemoveNodeIds([]);
    setRemoveRelationshipIds([]);
    setProposal(null);
    setError(null);
  }

  function closeReview() {
    if (saving) return;
    setSelected(null);
    setProposal(null);
  }

  async function createProposal(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected?.merge_commit_sha) {
      setError("Run reconciliation again so DPT can verify the PR merge commit.");
      return;
    }
    setSaving(true);
    setError(null);
    const now = new Date().toISOString();
    const operations: Array<Record<string, unknown>> = [];
    for (const relationshipId of removeRelationshipIds) {
      operations.push({ kind: "remove_graph_relationship", relationship_id: relationshipId });
    }
    for (const nodeId of removeNodeIds) {
      operations.push({ kind: "remove_graph_node", node_id: nodeId });
    }
    for (const node of nodes) {
      operations.push({ kind: "upsert_graph_node", node });
    }
    for (const relationship of relationships) {
      operations.push({ kind: "upsert_graph_relationship", relationship });
    }
    operations.push({
      kind: "set_graph_repository_snapshot",
      snapshot: {
        repository_alias: selected.repository_alias,
        commit_sha: selected.merge_commit_sha,
      },
    });
    operations.push({
      kind: "review_merged_pull_request",
      repository_alias: selected.repository_alias,
      pull_request_number: selected.pull_request_number,
      milestone_step_id: selected.milestone_step_id,
      merge_commit_sha: selected.merge_commit_sha,
      disposition,
      reason,
      reviewed_at: now,
      reviewed_by: reviewer,
    });
    try {
      const result = await api<PlanChangeProposal>(
        `/api/projects/${dashboard.project.project_id}/plan-proposals`,
        {
          method: "POST",
          body: JSON.stringify({
            project_id: dashboard.project.project_id,
            base_working_plan_version: graph?.working_plan_version,
            rationale: `Reconcile ${selected.change_set_id} with the cross-repository implementation graph.`,
            idempotency_key: `web-graph-${selected.change_set_id}-${selected.merge_commit_sha}`,
            client_name: "StateForge DPT WebApp",
            operations,
          }),
        },
      );
      setProposal(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The review proposal could not be created");
    } finally {
      setSaving(false);
    }
  }

  async function applyProposal() {
    if (!proposal) return;
    setSaving(true);
    setError(null);
    try {
      await api(`/api/plan-proposals/${proposal.proposal_id}/decision`, {
        method: "POST",
        body: JSON.stringify({
          decision: "apply",
          proposal_version: proposal.proposal_version,
          review_digest: proposal.review_digest,
          reason: "Reviewed and confirmed in the relationship graph editor.",
        }),
      });
      await onChanged();
      await load();
      setSelected(null);
      setProposal(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The graph review could not be applied");
    } finally {
      setSaving(false);
    }
  }

  function updateNode(index: number, changes: Partial<NodeDraft>) {
    setNodes((current) => current.map((node, item) => item === index ? { ...node, ...changes } : node));
  }

  function updateRelationship(index: number, changes: Partial<RelationshipDraft>) {
    setRelationships((current) => current.map((relationship, item) => item === index ? { ...relationship, ...changes } : relationship));
  }

  if (loading && !graph) return <Loading label="Loading implementation graph" />;

  return (
    <>
      <PageHeader
        eyebrow="Architecture integrity"
        title="Cross-repository graph"
        description="Trace protocol contracts into Provider and Agent implementations, then reconcile every merged milestone before DPT reports the plan as current."
        actions={<><button className="button button-secondary" disabled={loading} onClick={() => void verify()}><CheckCircle2 />Verify symbols</button><button className="button button-secondary" disabled={loading} onClick={() => void load()}><RefreshCw />Refresh</button></>}
      />
      {error && !selected && <ErrorBanner message={error} />}
      {verification && <section className={`graph-verification-result graph-verification-result-${verification.verification_status}`}><CheckCircle2 /><span><strong>Graph verification: {verification.verification_status}</strong><small>{verification.failures.length ? `${verification.failures.length} drift findings require review.` : "Every available registered symbol resolves at its pinned revision."}</small></span></section>}
      {graph && (
        <>
          <section className={`graph-deployment graph-deployment-${graph.deployment_status}`}>
            <div><Network /><span><small>Deployment state</small><strong>{graph.deployment_status.replaceAll("_", " ")}</strong></span></div>
            <dl>
              <div><dt>Working graph</dt><dd>r{graph.graph_revision}</dd></div>
              <div><dt>Published graph</dt><dd>r{graph.published_graph_revision}</dd></div>
              <div><dt>Symbols</dt><dd>{graph.nodes.length}</dd></div>
              <div><dt>Relationships</dt><dd>{graph.relationships.length}</dd></div>
            </dl>
          </section>

          {graph.deployment_status === "reconciling" && (
            <section className="graph-publish-required"><GitMerge /><span><strong>Review complete; deployment is still required</strong><small>Use Review &amp; publish in Plan editor to write the updated graph into STATEFORGE_DPT.md.</small></span></section>
          )}

          {graph.pending_redeployments.length > 0 && (
            <section className="graph-redeployment-inbox">
              <header><div><GitMerge /><span><h2>DPT redeployment required</h2><p>Merged milestones must be reconciled before the graph is current.</p></span></div><strong>{graph.pending_redeployments.length}</strong></header>
              <div>
                {graph.pending_redeployments.map((item) => (
                  <article key={item.change_set_id}>
                    <code>{item.milestone_step_id}</code>
                    <span><strong>{item.title}</strong><small>{item.github_full_name} #{item.pull_request_number} · {item.changed_files.length} changed files</small></span>
                    <button className="button button-primary button-small" disabled={!item.merge_commit_sha} onClick={() => beginReview(item)}>Review merge<ArrowRight /></button>
                  </article>
                ))}
              </div>
            </section>
          )}

          {graph.nodes.length ? (
            <section className="graph-explorer">
              <header><div><span className="eyebrow">Registered symbols</span><h2>Implementation counterparts</h2></div></header>
              <div className="graph-repository-columns">
                {dashboard.repositories.map((repository) => (
                  <article key={repository.alias}>
                    <header><code>{repository.alias}</code><span>{nodesByRepository.get(repository.alias)?.length || 0}</span></header>
                    {(nodesByRepository.get(repository.alias) || []).map((node) => (
                      <div className="graph-node" key={node.node_id}><small>{node.kind.replaceAll("_", " ")}</small><strong>{node.label}</strong><code>{node.symbol || node.path}</code></div>
                    ))}
                  </article>
                ))}
              </div>
              <div className="graph-relationship-list">
                {graph.relationships.map((relationship) => {
                  const source = nodeById.get(relationship.source_node_id);
                  const target = nodeById.get(relationship.target_node_id);
                  return <article key={relationship.relationship_id}><span><code>{source?.repository_alias}</code><strong>{source?.label || relationship.source_node_id}</strong></span><em>{relationship.relationship_type.replaceAll("_", " ")}<ArrowRight /></em><span><code>{target?.repository_alias}</code><strong>{target?.label || relationship.target_node_id}</strong></span><small className={`graph-verification-${relationship.verification.state}`}>{relationship.verification.state}</small></article>;
                })}
              </div>
            </section>
          ) : (
            <EmptyState icon={<Network />} title="No relationships registered" description="The first SFP reconciliation will map protocol contracts to their Provider and Agent counterparts." />
          )}
        </>
      )}

      {selected && (
        <Modal title={`Review ${selected.change_set_id}`} description="This creates an immutable plan proposal. Publishing the DPT file remains a separate step." onClose={closeReview} wide>
          {error && <ErrorBanner message={error} />}
          {proposal ? (
            <div className="graph-proposal-review">
              <div className="graph-review-digest"><CheckCircle2 /><span><strong>Exact proposal ready</strong><code>{proposal.review_digest}</code></span></div>
              <div className="github-markdown"><Markdown>{proposal.review_markdown}</Markdown></div>
              <div className="modal-actions"><button className="button button-ghost" disabled={saving} onClick={closeReview}>Cancel</button><button className="button button-primary" disabled={saving} onClick={() => void applyProposal()}>{saving ? "Applying…" : "Apply to working plan"}</button></div>
            </div>
          ) : (
            <form className="graph-review-form" onSubmit={createProposal}>
              <div className="segmented"><button className={disposition === "graph_updated" ? "active" : ""} onClick={() => setDisposition("graph_updated")} type="button">Graph changed</button><button className={disposition === "no_cross_repository_impact" ? "active" : ""} onClick={() => setDisposition("no_cross_repository_impact")} type="button">No cross-repository impact</button></div>
              <label><span>Review reason</span><textarea onChange={(event) => setReason(event.target.value)} placeholder={disposition === "graph_updated" ? "Explain what this PR adds or changes across repositories." : "Explain why this merged milestone does not change the graph."} required={disposition === "no_cross_repository_impact"} value={reason} /></label>

              {disposition === "graph_updated" && (
                <>
                  <section className="graph-edit-section"><header><div><h3>Symbols</h3><p>Add or replace exact repository symbols affected by this PR.</p></div><button className="button button-secondary button-small" onClick={() => setNodes((current) => [...current, emptyNode(selected.repository_alias)])} type="button"><Plus />Add symbol</button></header>{nodes.map((node, index) => <article className="graph-edit-card" key={`node-${index}`}><button className="graph-edit-remove" onClick={() => setNodes((current) => current.filter((_, item) => item !== index))} type="button"><X /></button><div className="form-grid"><label><span>Stable node ID</span><input onChange={(event) => updateNode(index, { node_id: event.target.value })} placeholder="agent.trigger-publication.consume" required value={node.node_id} /></label><label><span>Repository</span><select onChange={(event) => updateNode(index, { repository_alias: event.target.value })} value={node.repository_alias}>{dashboard.repositories.map((repository) => <option key={repository.alias} value={repository.alias}>{repository.alias}</option>)}</select></label><label><span>Kind</span><select onChange={(event) => updateNode(index, { kind: event.target.value as NodeDraft["kind"] })} value={node.kind}>{nodeKinds.map((kind) => <option key={kind} value={kind}>{kind.replaceAll("_", " ")}</option>)}</select></label><label><span>Label</span><input onChange={(event) => updateNode(index, { label: event.target.value })} required value={node.label} /></label><label><span>Repository path</span><input onChange={(event) => updateNode(index, { path: event.target.value })} required value={node.path} /></label><label><span>Qualified symbol</span><input onChange={(event) => updateNode(index, { symbol: event.target.value || null })} value={node.symbol || ""} /></label></div></article>)}</section>
                  <section className="graph-edit-section"><header><div><h3>Relationships</h3><p>Connect the selected PR’s symbols to existing or newly added counterparts.</p></div><button className="button button-secondary button-small" onClick={() => setRelationships((current) => [...current, emptyRelationship(selected.change_set_id)])} type="button"><Plus />Add relationship</button></header>{relationships.map((relationship, index) => <article className="graph-edit-card" key={`relationship-${index}`}><button className="graph-edit-remove" onClick={() => setRelationships((current) => current.filter((_, item) => item !== index))} type="button"><X /></button><div className="form-grid"><label><span>Stable relationship ID</span><input onChange={(event) => updateRelationship(index, { relationship_id: event.target.value })} required value={relationship.relationship_id} /></label><label><span>Type</span><select onChange={(event) => updateRelationship(index, { relationship_type: event.target.value as RelationshipDraft["relationship_type"] })} value={relationship.relationship_type}>{relationshipTypes.map((kind) => <option key={kind} value={kind}>{kind.replaceAll("_", " ")}</option>)}</select></label><label><span>Source node ID</span><input onChange={(event) => updateRelationship(index, { source_node_id: event.target.value })} required value={relationship.source_node_id} /></label><label><span>Target node ID</span><input onChange={(event) => updateRelationship(index, { target_node_id: event.target.value })} required value={relationship.target_node_id} /></label></div></article>)}</section>
                  {(graph?.relationships.length || graph?.nodes.length) ? <details className="graph-removal-editor"><summary>Remove stale graph records</summary><div><h4>Relationships</h4>{graph?.relationships.map((relationship) => <label key={relationship.relationship_id}><input checked={removeRelationshipIds.includes(relationship.relationship_id)} onChange={(event) => setRemoveRelationshipIds((current) => event.target.checked ? [...current, relationship.relationship_id] : current.filter((value) => value !== relationship.relationship_id))} type="checkbox" />{relationship.relationship_id}</label>)}<h4>Nodes</h4>{graph?.nodes.map((node) => <label key={node.node_id}><input checked={removeNodeIds.includes(node.node_id)} onChange={(event) => setRemoveNodeIds((current) => event.target.checked ? [...current, node.node_id] : current.filter((value) => value !== node.node_id))} type="checkbox" />{node.node_id}</label>)}</div></details> : null}
                </>
              )}
              <div className="modal-actions"><button className="button button-ghost" disabled={saving} onClick={closeReview} type="button">Cancel</button><button className="button button-primary" disabled={saving || !selected.merge_commit_sha}>{saving ? "Preparing proposal…" : "Review exact proposal"}</button></div>
            </form>
          )}
        </Modal>
      )}
    </>
  );
}
