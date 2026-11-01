import {
  ArrowRight,
  CheckCircle2,
  ExternalLink,
  GitMerge,
  GitPullRequest,
  History,
  List,
  Network,
  Plus,
  RefreshCw,
  Search,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useState,
  type Dispatch,
  type FormEvent,
  type SetStateAction,
} from "react";

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
  "procedure", "protobuf_package", "service", "rpc", "message",
  "python_module", "class", "function", "storage_contract",
  "storage_implementation", "test",
];
const relationshipTypes: RelationshipGraphRelationship["relationship_type"][] = [
  "implements", "consumes", "generated_from", "validates", "persists",
  "translates", "exposes", "tests",
];
type GraphTab = "graph" | "registry" | "reviews";
type NodeDraft = RelationshipGraphNode;
type RelationshipDraft = RelationshipGraphRelationship;

function slug(value: string) {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, ".").replace(/^\.+|\.+$/g, "");
}

function queryState(): { tab: GraphTab; node: string | null } {
  const values = new URLSearchParams(window.location.hash.split("?", 2)[1] || "");
  const requested = values.get("tab");
  return {
    tab: requested === "registry" || requested === "reviews" ? requested : "graph",
    node: values.get("node"),
  };
}

function replaceQuery(tab: GraphTab, node?: string | null) {
  const base = window.location.hash.split("?", 1)[0];
  const values = new URLSearchParams();
  if (tab !== "graph") values.set("tab", tab);
  if (node) values.set("node", node);
  const query = values.toString();
  window.history.replaceState(null, "", `${base}${query ? `?${query}` : ""}`);
}

function emptyNode(repositoryAlias: string): NodeDraft {
  return {
    node_id: "", repository_alias: repositoryAlias, kind: "function", label: "",
    path: "", symbol: null, parent_node_id: null, procedure_id: null,
    implementation_state: "implemented",
  };
}

function emptyRelationship(changeSetId: string): RelationshipDraft {
  return {
    relationship_id: "", source_node_id: "", target_node_id: "",
    relationship_type: "consumes", directness: "direct",
    introduced_by_change_set_id: changeSetId,
    verification: { method: "manual", details: "" },
  };
}

function SourceLink({ node, compact = false }: { node: RelationshipGraphNode; compact?: boolean }) {
  if (node.implementation_state === "expected") {
    return <span className="graph-expected-label">planned</span>;
  }
  if (!node.github_url) {
    return <span className="graph-unresolved-label">verify to locate</span>;
  }
  const line = node.observation?.line_start;
  return (
    <a className="graph-source-link" href={node.github_url} rel="noreferrer" target="_blank">
      {compact ? (line ? `L${line}` : "source") : (line ? `${node.path}:${line}` : node.path)}
      <ExternalLink />
    </a>
  );
}

function Topology({
  nodes, relationships, repositories, selectedNodeId, onSelect,
}: {
  nodes: RelationshipGraphNode[];
  relationships: RelationshipGraphRelationship[];
  repositories: Dashboard["repositories"];
  selectedNodeId: string | null;
  onSelect: (nodeId: string | null) => void;
}) {
  const [zoom, setZoom] = useState(1);
  const laneAliases = useMemo(() => {
    const present = new Set(nodes.map((node) => node.repository_alias));
    const preferred = ["provider", "protocol", "agent"];
    return [
      ...preferred.filter((alias) => present.has(alias)),
      ...repositories.map((item) => item.alias)
        .filter((alias) => present.has(alias) && !preferred.includes(alias)),
    ];
  }, [nodes, repositories]);
  const laneNodes = useMemo(
    () => new Map(laneAliases.map((alias) => [
      alias,
      nodes.filter((node) => node.repository_alias === alias).sort((left, right) => {
        const rank: Record<string, number> = {
          python_module: 0, class: 0, service: 1, function: 2, rpc: 2,
          storage_contract: 2, storage_implementation: 3, message: 3,
        };
        return (rank[left.kind] ?? 4) - (rank[right.kind] ?? 4)
          || left.label.localeCompare(right.label);
      }),
    ])),
    [laneAliases, nodes],
  );
  const positions = useMemo(() => {
    const values = new Map<string, { x: number; y: number }>();
    laneAliases.forEach((alias, lane) => {
      (laneNodes.get(alias) || []).forEach((node, index) => {
        values.set(node.node_id, { x: 42 + lane * 330, y: 82 + index * 88 });
      });
    });
    return values;
  }, [laneAliases, laneNodes]);
  const visibleRelationships = relationships.filter(
    (item) => positions.has(item.source_node_id) && positions.has(item.target_node_id),
  );
  const parentLinks = nodes.filter(
    (node) => node.parent_node_id && positions.has(node.parent_node_id),
  );
  const connected = new Set<string>();
  if (selectedNodeId) {
    connected.add(selectedNodeId);
    visibleRelationships.forEach((item) => {
      if (item.source_node_id === selectedNodeId) connected.add(item.target_node_id);
      if (item.target_node_id === selectedNodeId) connected.add(item.source_node_id);
    });
  }
  const height = Math.max(
    330,
    ...laneAliases.map((alias) => (laneNodes.get(alias)?.length || 0) * 88 + 130),
  );
  const width = Math.max(760, laneAliases.length * 330 + 24);
  if (!nodes.length) {
    return <EmptyState icon={<Network />} title="No symbols match" description="Change the procedure or filters to reveal a relationship neighborhood." />;
  }
  return (
    <section className="topology-shell">
      <div className="topology-toolbar">
        <span>{nodes.length} unique symbols · {visibleRelationships.length} relationships</span>
        <div>
          {selectedNodeId && <button className="button button-ghost button-small" onClick={() => onSelect(null)} type="button">Clear focus</button>}
          <button aria-label="Zoom out" className="button button-secondary button-small" onClick={() => setZoom((value) => Math.max(.65, value - .15))} type="button"><ZoomOut /></button>
          <strong>{Math.round(zoom * 100)}%</strong>
          <button aria-label="Zoom in" className="button button-secondary button-small" onClick={() => setZoom((value) => Math.min(1.6, value + .15))} type="button"><ZoomIn /></button>
        </div>
      </div>
      <div className="topology-scroll">
        <svg aria-label="Cross-repository relationship topology" className="topology" height={height * zoom} role="img" style={{ height: height * zoom, width: width * zoom }} viewBox={`0 0 ${width} ${height}`} width={width * zoom}>
          <defs><marker id="graph-arrow" markerHeight="7" markerWidth="7" orient="auto" refX="7" refY="3.5"><path d="M0,0 L7,3.5 L0,7 Z" /></marker></defs>
          {laneAliases.map((alias, index) => <g className="topology-lane" key={alias}><rect height={height - 24} rx="14" width="292" x={24 + index * 330} y="12" /><text x={46 + index * 330} y="45">{alias}</text><text className="topology-lane-count" x={278 + index * 330} y="45">{laneNodes.get(alias)?.length || 0}</text></g>)}
          <g className="topology-parent-edges">
            {parentLinks.map((node) => {
              const parent = positions.get(node.parent_node_id!)!;
              const child = positions.get(node.node_id)!;
              return <path d={`M ${parent.x + 125} ${parent.y + 58} C ${parent.x + 125} ${(parent.y + child.y) / 2}, ${child.x + 125} ${(parent.y + child.y) / 2}, ${child.x + 125} ${child.y}`} key={`${node.parent_node_id}:${node.node_id}`}><title>contains {node.label}</title></path>;
            })}
          </g>
          <g className="topology-edges">
            {visibleRelationships.map((item) => {
              const source = positions.get(item.source_node_id)!;
              const target = positions.get(item.target_node_id)!;
              const sx = source.x + (source.x < target.x ? 250 : 0);
              const tx = target.x + (source.x < target.x ? 0 : 250);
              const sy = source.y + 28;
              const ty = target.y + 28;
              const midpoint = (sx + tx) / 2;
              const active = !selectedNodeId || item.source_node_id === selectedNodeId || item.target_node_id === selectedNodeId;
              return <path className={`${item.directness} ${item.observation?.state || "unverified"} ${active ? "active" : "muted"}`} d={`M ${sx} ${sy} C ${midpoint} ${sy}, ${midpoint} ${ty}, ${tx} ${ty}`} key={item.relationship_id} markerEnd="url(#graph-arrow)"><title>{item.relationship_type} · {item.directness} · {item.observation?.detail || "not verified"}</title></path>;
            })}
          </g>
          <g className="topology-nodes">
            {nodes.map((node) => {
              const position = positions.get(node.node_id)!;
              const active = !selectedNodeId || connected.has(node.node_id);
              return <g aria-label={`${node.repository_alias}: ${node.label}`} className={`${node.implementation_state} ${node.observation?.state || "unverified"} ${active ? "active" : "muted"} ${selectedNodeId === node.node_id ? "selected" : ""}`} key={node.node_id} onClick={() => onSelect(node.node_id)} role="button" tabIndex={0} transform={`translate(${position.x} ${position.y})`}><rect height="58" rx="10" width="250" /><text className="topology-node-kind" x="13" y="19">{node.kind.replaceAll("_", " ")}</text><text className="topology-node-label" x="13" y="40">{node.label.length > 29 ? `${node.label.slice(0, 28)}…` : node.label}</text><circle cx="231" cy="18" r="5"><title>{node.observation?.state || "not verified"}</title></circle></g>;
            })}
          </g>
        </svg>
      </div>
      {!selectedNodeId && <footer>Click a symbol to isolate its direct impact neighborhood.</footer>}
    </section>
  );
}

export function RelationshipGraphPage({
  dashboard, reviewer, onChanged,
}: {
  dashboard: Dashboard;
  reviewer: string;
  onChanged: () => Promise<void>;
}) {
  const initial = queryState();
  const [tab, setTabState] = useState<GraphTab>(initial.tab);
  const [graph, setGraph] = useState<RelationshipGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeIdState] = useState<string | null>(initial.node);
  const [procedure, setProcedure] = useState("");
  const [repositoryFilter, setRepositoryFilter] = useState("");
  const [search, setSearch] = useState("");
  const [registryPage, setRegistryPage] = useState(1);
  const [selected, setSelected] = useState<PendingGraphRedeployment | null>(null);
  const [disposition, setDisposition] = useState<"graph_updated" | "no_cross_repository_impact">("graph_updated");
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

  function setTab(value: GraphTab) {
    setTabState(value);
    replaceQuery(value, selectedNodeId);
  }
  function setSelectedNodeId(value: string | null) {
    setSelectedNodeIdState(value);
    replaceQuery(tab, value);
  }
  async function load() {
    setLoading(true);
    setError(null);
    try {
      const result = await api<RelationshipGraph>(`/api/projects/${dashboard.project.project_id}/relationship-graph`);
      setGraph(result);
      const values = [...new Set(result.nodes.map((node) => node.procedure_id).filter(Boolean))] as string[];
      if (!procedure && values.length) {
        setProcedure(values.includes("bootstrap") ? "bootstrap" : values[0]);
      }
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
      const result = await api<{
        verification_status: string;
        failures: Array<{ node_id?: string; relationship_id?: string; detail: string }>;
      }>(`/api/projects/${dashboard.project.project_id}/relationship-graph/verify`, { method: "POST" });
      setVerification(result);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Graph verification failed");
      setLoading(false);
    }
  }
  useEffect(() => { void load(); }, [dashboard.project.project_id, dashboard.project.working_plan_version]);

  const nodeById = useMemo(() => new Map((graph?.nodes || []).map((node) => [node.node_id, node])), [graph]);
  const procedures = useMemo(() => [...new Set((graph?.nodes || []).map((node) => node.procedure_id).filter(Boolean))].sort() as string[], [graph]);
  const filteredNodes = useMemo(() => {
    const query = search.trim().toLowerCase();
    return (graph?.nodes || []).filter((node) =>
      (!procedure || node.procedure_id === procedure)
      && (!repositoryFilter || node.repository_alias === repositoryFilter)
      && (!query || `${node.label} ${node.symbol || ""} ${node.path}`.toLowerCase().includes(query)),
    );
  }, [graph, procedure, repositoryFilter, search]);
  const filteredNodeIds = useMemo(() => new Set(filteredNodes.map((node) => node.node_id)), [filteredNodes]);
  const filteredRelationships = useMemo(
    () => (graph?.relationships || []).filter((item) => filteredNodeIds.has(item.source_node_id) && filteredNodeIds.has(item.target_node_id)),
    [graph, filteredNodeIds],
  );
  const selectedNode = selectedNodeId ? nodeById.get(selectedNodeId) || null : null;
  const selectedRelationships = selectedNodeId
    ? (graph?.relationships || []).filter((item) => item.source_node_id === selectedNodeId || item.target_node_id === selectedNodeId)
    : [];
  const registryNodes = filteredNodes;
  const registryPageSize = 30;
  const registryPageNodes = registryNodes.slice((registryPage - 1) * registryPageSize, registryPage * registryPageSize);
  const relationshipCounts = useMemo(() => {
    const counts = new Map<string, number>();
    (graph?.relationships || []).forEach((item) => {
      counts.set(item.source_node_id, (counts.get(item.source_node_id) || 0) + 1);
      counts.set(item.target_node_id, (counts.get(item.target_node_id) || 0) + 1);
    });
    return counts;
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
    if (!saving) { setSelected(null); setProposal(null); }
  }
  async function createProposal(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected?.merge_commit_sha) {
      setError("Run reconciliation again so DPT can verify the PR merge commit.");
      return;
    }
    setSaving(true);
    setError(null);
    const operations: Array<Record<string, unknown>> = [];
    removeRelationshipIds.forEach((relationship_id) => operations.push({ kind: "remove_graph_relationship", relationship_id }));
    removeNodeIds.forEach((node_id) => operations.push({ kind: "remove_graph_node", node_id }));
    nodes.forEach((node) => operations.push({ kind: "upsert_graph_node", node }));
    relationships.forEach((relationship) => operations.push({ kind: "upsert_graph_relationship", relationship }));
    operations.push({
      kind: "set_graph_repository_snapshot",
      snapshot: { repository_alias: selected.repository_alias, commit_sha: selected.merge_commit_sha },
    });
    operations.push({
      kind: "review_merged_pull_request",
      repository_alias: selected.repository_alias,
      pull_request_number: selected.pull_request_number,
      milestone_step_id: selected.milestone_step_id,
      merge_commit_sha: selected.merge_commit_sha,
      disposition,
      reason,
      reviewed_at: new Date().toISOString(),
      reviewed_by: reviewer,
    });
    try {
      setProposal(await api<PlanChangeProposal>(
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
      ));
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
    setRelationships((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...changes } : item));
  }

  if (loading && !graph) return <Loading label="Loading implementation graph" />;
  return (
    <>
      <PageHeader
        eyebrow="Architecture integrity"
        title="Cross-repository graph"
        description="See the actual path from protocol contracts to Provider and Agent code, with immutable source evidence and post-merge drift control."
        actions={<><button className="button button-secondary" disabled={loading} onClick={() => void verify()}><CheckCircle2 />Verify code</button><button className="button button-secondary" disabled={loading} onClick={() => void load()}><RefreshCw />Refresh</button></>}
      />
      {error && !selected && <ErrorBanner message={error} />}
      {verification && <section className={`graph-verification-result graph-verification-result-${verification.verification_status}`}><CheckCircle2 /><span><strong>Verification: {verification.verification_status}</strong><small>{verification.failures.length ? `${verification.failures.length} drift findings require review.` : "Symbols and direct relationships were checked at pinned commits."}</small></span></section>}
      {graph && <GraphContent
        dashboard={dashboard}
        graph={graph}
        tab={tab}
        setTab={setTab}
        procedure={procedure}
        setProcedure={setProcedure}
        procedures={procedures}
        repositoryFilter={repositoryFilter}
        setRepositoryFilter={setRepositoryFilter}
        search={search}
        setSearch={setSearch}
        registryPage={registryPage}
        setRegistryPage={setRegistryPage}
        registryPageNodes={registryPageNodes}
        registryNodes={registryNodes}
        registryPageSize={registryPageSize}
        relationshipCounts={relationshipCounts}
        filteredNodes={filteredNodes}
        filteredRelationships={filteredRelationships}
        selectedNode={selectedNode}
        selectedNodeId={selectedNodeId}
        setSelectedNodeId={setSelectedNodeId}
        selectedRelationships={selectedRelationships}
        nodeById={nodeById}
        beginReview={beginReview}
      />}
      {selected && <ReviewModal
        closeReview={closeReview}
        createProposal={createProposal}
        dashboard={dashboard}
        disposition={disposition}
        error={error}
        graph={graph}
        nodes={nodes}
        proposal={proposal}
        reason={reason}
        relationships={relationships}
        removeNodeIds={removeNodeIds}
        removeRelationshipIds={removeRelationshipIds}
        saving={saving}
        selected={selected}
        setDisposition={setDisposition}
        setNodes={setNodes}
        setReason={setReason}
        setRelationships={setRelationships}
        setRemoveNodeIds={setRemoveNodeIds}
        setRemoveRelationshipIds={setRemoveRelationshipIds}
        updateNode={updateNode}
        updateRelationship={updateRelationship}
        applyProposal={applyProposal}
        procedures={procedures}
      />}
    </>
  );
}

type GraphContentProps = {
  dashboard: Dashboard;
  graph: RelationshipGraph;
  tab: GraphTab;
  setTab: (value: GraphTab) => void;
  procedure: string;
  setProcedure: (value: string) => void;
  procedures: string[];
  repositoryFilter: string;
  setRepositoryFilter: (value: string) => void;
  search: string;
  setSearch: (value: string) => void;
  registryPage: number;
  setRegistryPage: Dispatch<SetStateAction<number>>;
  registryPageNodes: RelationshipGraphNode[];
  registryNodes: RelationshipGraphNode[];
  registryPageSize: number;
  relationshipCounts: Map<string, number>;
  filteredNodes: RelationshipGraphNode[];
  filteredRelationships: RelationshipGraphRelationship[];
  selectedNode: RelationshipGraphNode | null;
  selectedNodeId: string | null;
  setSelectedNodeId: (value: string | null) => void;
  selectedRelationships: RelationshipGraphRelationship[];
  nodeById: Map<string, RelationshipGraphNode>;
  beginReview: (item: PendingGraphRedeployment) => void;
};

function GraphContent(props: GraphContentProps) {
  const {
    dashboard, graph, tab, setTab, procedure, setProcedure, procedures,
    repositoryFilter, setRepositoryFilter, search, setSearch, registryPage,
    setRegistryPage, registryPageNodes, registryNodes, registryPageSize,
    relationshipCounts, filteredNodes, filteredRelationships, selectedNode,
    selectedNodeId, setSelectedNodeId, selectedRelationships, nodeById,
    beginReview,
  } = props;
  return (
    <>
      <section className={`graph-deployment graph-deployment-${graph.deployment_status}`}>
        <div><Network /><span><small>Graph state</small><strong>{graph.deployment_status.replaceAll("_", " ")}</strong></span></div>
        <dl>
          <div><dt>Checked</dt><dd>{graph.metrics.evidence_coverage}%</dd></div>
          <div><dt>Drift</dt><dd>{graph.metrics.drift_findings}</dd></div>
          <div><dt>Implemented</dt><dd>{graph.metrics.implemented_nodes}</dd></div>
          <div><dt>Planned gaps</dt><dd>{graph.metrics.expected_nodes}</dd></div>
          <div><dt>Procedures</dt><dd>{graph.metrics.procedures}</dd></div>
        </dl>
      </section>
      {graph.deployment_status === "reconciling" && <button className="graph-publish-required" onClick={() => { window.location.hash = `/projects/${dashboard.project.project_id}/plan`; }} type="button"><GitMerge /><span><strong>Review complete; publish the DPT file</strong><small>Open Plan editor to review and deploy graph r{graph.graph_revision} into STATEFORGE_DPT.md.</small></span><ArrowRight /></button>}
      <nav className="graph-tabs" aria-label="Relationship graph views">
        <button className={tab === "graph" ? "active" : ""} onClick={() => setTab("graph")}><Network />Graph</button>
        <button className={tab === "registry" ? "active" : ""} onClick={() => setTab("registry")}><List />Registered symbols <span>{graph.nodes.length}</span></button>
        <button className={tab === "reviews" ? "active" : ""} onClick={() => setTab("reviews")}><GitPullRequest />Merge reviews {graph.pending_redeployments.length > 0 && <span className="warning">{graph.pending_redeployments.length}</span>}</button>
      </nav>
      {tab !== "reviews" && <section className="graph-filters">
        <label><span>Procedure</span><select onChange={(event) => { setProcedure(event.target.value); setRegistryPage(1); setSelectedNodeId(null); }} value={procedure}><option value="">All procedures</option>{procedures.map((item) => <option key={item} value={item}>{item.replaceAll("-", " ")}</option>)}</select></label>
        <label><span>Repository</span><select onChange={(event) => { setRepositoryFilter(event.target.value); setRegistryPage(1); }} value={repositoryFilter}><option value="">All repositories</option>{dashboard.repositories.filter((repository) => graph.nodes.some((node) => node.repository_alias === repository.alias)).map((repository) => <option key={repository.alias} value={repository.alias}>{repository.alias}</option>)}</select></label>
        <label className="graph-search"><span>Search</span><div><Search /><input onChange={(event) => { setSearch(event.target.value); setRegistryPage(1); }} placeholder="Symbol, path, or label" value={search} /></div></label>
      </section>}
      {tab === "graph" && <div className={`graph-workspace ${selectedNode ? "with-detail" : ""}`}>
        <Topology nodes={filteredNodes} onSelect={setSelectedNodeId} relationships={filteredRelationships} repositories={dashboard.repositories} selectedNodeId={selectedNodeId} />
        {selectedNode && <aside className="graph-detail">
          <button aria-label="Close symbol details" onClick={() => setSelectedNodeId(null)}><X /></button>
          <small>{selectedNode.repository_alias} · {selectedNode.kind.replaceAll("_", " ")}</small>
          <h2>{selectedNode.label}</h2>
          <code>{selectedNode.symbol || selectedNode.node_id}</code>
          <dl>
            <div><dt>Procedure</dt><dd>{selectedNode.procedure_id || "shared"}</dd></div>
            <div><dt>State</dt><dd>{selectedNode.implementation_state}</dd></div>
            <div><dt>Evidence</dt><dd>{selectedNode.observation?.state || "not run"}</dd></div>
            <div><dt>Pinned commit</dt><dd><code>{selectedNode.snapshot_sha?.slice(0, 12) || "not pinned"}</code></dd></div>
          </dl>
          <SourceLink node={selectedNode} />
          <p>{selectedNode.observation?.detail || "Run verification to resolve the exact source declaration."}</p>
          <h3>Impact neighborhood</h3>
          {selectedRelationships.map((item) => {
            const counterpartId = item.source_node_id === selectedNode.node_id ? item.target_node_id : item.source_node_id;
            const counterpart = nodeById.get(counterpartId);
            return <button className="graph-counterpart" key={item.relationship_id} onClick={() => counterpart && setSelectedNodeId(counterpart.node_id)}><span><strong>{counterpart?.label || counterpartId}</strong><small>{item.relationship_type} · {item.directness}</small></span><ArrowRight /></button>;
          })}
        </aside>}
      </div>}
      {tab === "registry" && <section className="graph-registry">
        <header><div><span className="eyebrow">Maintainable inventory</span><h2>Registered symbols</h2><p>One row per unique symbol. Source locations are derived at the pinned commit, never maintained by hand.</p></div><span>{registryNodes.length} results</span></header>
        <div className="graph-registry-table"><table><thead><tr><th>Symbol</th><th>Repository</th><th>Kind / procedure</th><th>Evidence</th><th>Relations</th><th>Source</th></tr></thead><tbody>{registryPageNodes.map((node) => <tr key={node.node_id}><td><button onClick={() => { setTab("graph"); setSelectedNodeId(node.node_id); setProcedure(node.procedure_id || ""); }}><strong>{node.label}</strong><code>{node.node_id}</code></button></td><td><code>{node.repository_alias}</code></td><td><span>{node.kind.replaceAll("_", " ")}</span><small>{node.procedure_id || "shared"}</small></td><td><span className={`graph-state graph-state-${node.observation?.state || node.implementation_state}`}>{node.observation?.state || node.implementation_state}</span></td><td>{relationshipCounts.get(node.node_id) || 0}</td><td><SourceLink compact node={node} /></td></tr>)}</tbody></table></div>
        <footer><span>Page {registryPage} of {Math.max(1, Math.ceil(registryNodes.length / registryPageSize))}</span><div><button className="button button-secondary button-small" disabled={registryPage === 1} onClick={() => setRegistryPage((value) => value - 1)}>Previous</button><button className="button button-secondary button-small" disabled={registryPage * registryPageSize >= registryNodes.length} onClick={() => setRegistryPage((value) => value + 1)}>Next</button></div></footer>
      </section>}
      {tab === "reviews" && <>
        <section className="graph-redeployment-inbox"><header><div><GitMerge /><span><h2>Merged milestone reviews</h2><p>Select the exact merged PR, record its graph impact, then publish through Plan editor.</p></span></div><strong>{graph.pending_redeployments.length}</strong></header>{graph.pending_redeployments.length ? <div>{graph.pending_redeployments.map((item) => <article key={item.change_set_id}><code>{item.milestone_step_id}</code><span><strong>{item.title}</strong><small>{item.github_full_name} #{item.pull_request_number} · {item.changed_files.length} changed files</small></span><a className="button button-secondary button-small" href={item.html_url} rel="noreferrer" target="_blank">PR <ExternalLink /></a><button className="button button-primary button-small" disabled={!item.merge_commit_sha} onClick={() => beginReview(item)}>Review merge<ArrowRight /></button></article>)}</div> : <EmptyState icon={<CheckCircle2 />} title="No unreviewed milestone merges" description="The graph is reconciled with every detected merged milestone." />}</section>
        <section className="graph-history"><header><History /><span><h2>Graph provenance</h2><p>Every graph-changing review remains tied to its selected merged PR.</p></span></header>{graph.change_sets.length ? graph.change_sets.map((item) => <article key={item.change_set_id}><span><code>{item.change_set_id}</code><strong>{item.milestone_step_id} · {item.disposition.replaceAll("_", " ")}</strong><small>{item.reason || "Graph relationships updated."}</small></span><span><small>{item.reviewed_by} · {new Date(item.reviewed_at).toLocaleString()}</small><a href={item.html_url} rel="noreferrer" target="_blank">Open PR <ExternalLink /></a></span></article>) : <p className="graph-history-empty">Initial baseline · no post-baseline merge reviews yet.</p>}</section>
      </>}
    </>
  );
}

type ReviewModalProps = {
  selected: PendingGraphRedeployment;
  graph: RelationshipGraph | null;
  dashboard: Dashboard;
  procedures: string[];
  disposition: "graph_updated" | "no_cross_repository_impact";
  setDisposition: (value: "graph_updated" | "no_cross_repository_impact") => void;
  reason: string;
  setReason: (value: string) => void;
  nodes: NodeDraft[];
  setNodes: Dispatch<SetStateAction<NodeDraft[]>>;
  relationships: RelationshipDraft[];
  setRelationships: Dispatch<SetStateAction<RelationshipDraft[]>>;
  removeNodeIds: string[];
  setRemoveNodeIds: Dispatch<SetStateAction<string[]>>;
  removeRelationshipIds: string[];
  setRemoveRelationshipIds: Dispatch<SetStateAction<string[]>>;
  proposal: PlanChangeProposal | null;
  saving: boolean;
  error: string | null;
  closeReview: () => void;
  createProposal: (event: FormEvent<HTMLFormElement>) => Promise<void>;
  applyProposal: () => Promise<void>;
  updateNode: (index: number, changes: Partial<NodeDraft>) => void;
  updateRelationship: (index: number, changes: Partial<RelationshipDraft>) => void;
};

function ReviewModal(props: ReviewModalProps) {
  const {
    selected, graph, dashboard, procedures, disposition, setDisposition,
    reason, setReason, nodes, setNodes, relationships, setRelationships,
    removeNodeIds, setRemoveNodeIds, removeRelationshipIds,
    setRemoveRelationshipIds, proposal, saving, error, closeReview,
    createProposal, applyProposal, updateNode, updateRelationship,
  } = props;
  if (proposal) {
    return <Modal title={`Review ${selected.change_set_id}`} description="The exact immutable plan proposal is ready for a final decision." onClose={closeReview} wide><div className="graph-proposal-review"><div className="graph-review-digest"><CheckCircle2 /><span><strong>Exact proposal ready</strong><code>{proposal.review_digest}</code></span></div><div className="github-markdown"><Markdown>{proposal.review_markdown}</Markdown></div><div className="modal-actions"><button className="button button-ghost" disabled={saving} onClick={closeReview}>Cancel</button><button className="button button-primary" disabled={saving} onClick={() => void applyProposal()}>{saving ? "Applying…" : "Apply to working plan"}</button></div></div></Modal>;
  }
  const choices = [...(graph?.nodes || []), ...nodes];
  return (
    <Modal title={`Review ${selected.change_set_id}`} description="Register only the symbols and relationships changed by this exact merged PR." onClose={closeReview} wide>
      {error && <ErrorBanner message={error} />}
      <form className="graph-review-form" onSubmit={createProposal}>
        <section className="graph-review-pr"><GitPullRequest /><span><strong>{selected.title}</strong><small>{selected.github_full_name} #{selected.pull_request_number} · {selected.changed_files.length} changed files</small></span><a href={selected.html_url} rel="noreferrer" target="_blank">Open merged PR <ExternalLink /></a></section>
        <details className="graph-changed-files"><summary>Changed files</summary>{selected.changed_files.map((file) => <code key={file.filename}>{file.filename}</code>)}</details>
        <div className="segmented"><button className={disposition === "graph_updated" ? "active" : ""} onClick={() => setDisposition("graph_updated")} type="button">Graph changed</button><button className={disposition === "no_cross_repository_impact" ? "active" : ""} onClick={() => setDisposition("no_cross_repository_impact")} type="button">No cross-repository impact</button></div>
        <label><span>Review reason</span><textarea onChange={(event) => setReason(event.target.value)} placeholder="Explain the cross-repository impact or why there is none." required={disposition === "no_cross_repository_impact"} value={reason} /></label>
        {disposition === "graph_updated" && <>
          <section className="graph-edit-section">
            <header><div><h3>Symbols</h3><p>IDs are suggested from repository and label; exact lines are resolved automatically.</p></div><button className="button button-secondary button-small" onClick={() => setNodes((current) => [...current, emptyNode(selected.repository_alias)])} type="button"><Plus />Add symbol</button></header>
            {nodes.map((node, index) => <article className="graph-edit-card" key={`node-${index}`}>
              <button className="graph-edit-remove" onClick={() => setNodes((current) => current.filter((_, item) => item !== index))} type="button"><X /></button>
              <div className="form-grid">
                <label><span>Repository</span><select onChange={(event) => updateNode(index, { repository_alias: event.target.value })} value={node.repository_alias}>{dashboard.repositories.map((repository) => <option key={repository.alias} value={repository.alias}>{repository.alias}</option>)}</select></label>
                <label><span>Label</span><input onChange={(event) => updateNode(index, { label: event.target.value, node_id: node.node_id || `${node.repository_alias}.${slug(event.target.value)}` })} required value={node.label} /></label>
                <label><span>Stable ID</span><input onChange={(event) => updateNode(index, { node_id: event.target.value })} placeholder="generated from label" required value={node.node_id} /></label>
                <label><span>Kind</span><select onChange={(event) => updateNode(index, { kind: event.target.value as NodeDraft["kind"] })} value={node.kind}>{nodeKinds.map((kind) => <option key={kind} value={kind}>{kind.replaceAll("_", " ")}</option>)}</select></label>
                <label><span>Procedure</span><select onChange={(event) => updateNode(index, { procedure_id: event.target.value || null })} value={node.procedure_id || ""}><option value="">Shared</option>{procedures.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
                <label><span>Implementation</span><select onChange={(event) => updateNode(index, { implementation_state: event.target.value as NodeDraft["implementation_state"] })} value={node.implementation_state}><option value="implemented">Implemented</option><option value="expected">Planned gap</option></select></label>
                <label><span>Repository path</span><input list={`changed-files-${index}`} onChange={(event) => updateNode(index, { path: event.target.value })} required value={node.path} /><datalist id={`changed-files-${index}`}>{selected.changed_files.map((file) => <option key={file.filename} value={file.filename} />)}</datalist></label>
                <label><span>Qualified symbol</span><input onChange={(event) => updateNode(index, { symbol: event.target.value || null })} value={node.symbol || ""} /></label>
              </div>
            </article>)}
          </section>
          <section className="graph-edit-section">
            <header><div><h3>Relationships</h3><p>Select registered symbols; do not copy raw IDs by hand.</p></div><button className="button button-secondary button-small" onClick={() => setRelationships((current) => [...current, emptyRelationship(selected.change_set_id)])} type="button"><Plus />Add relationship</button></header>
            {relationships.map((relationship, index) => <article className="graph-edit-card" key={`relationship-${index}`}>
              <button className="graph-edit-remove" onClick={() => setRelationships((current) => current.filter((_, item) => item !== index))} type="button"><X /></button>
              <div className="form-grid">
                <label><span>Source symbol</span><select onChange={(event) => updateRelationship(index, { source_node_id: event.target.value })} required value={relationship.source_node_id}><option value="">Select…</option>{choices.map((node) => <option key={node.node_id} value={node.node_id}>{node.repository_alias} · {node.label}</option>)}</select></label>
                <label><span>Target symbol</span><select onChange={(event) => { const target = event.target.value; updateRelationship(index, { target_node_id: target, relationship_id: relationship.relationship_id || `${slug(relationship.source_node_id)}.${relationship.relationship_type}.${slug(target)}` }); }} required value={relationship.target_node_id}><option value="">Select…</option>{choices.map((node) => <option key={node.node_id} value={node.node_id}>{node.repository_alias} · {node.label}</option>)}</select></label>
                <label><span>Type</span><select onChange={(event) => updateRelationship(index, { relationship_type: event.target.value as RelationshipDraft["relationship_type"] })} value={relationship.relationship_type}>{relationshipTypes.map((kind) => <option key={kind} value={kind}>{kind.replaceAll("_", " ")}</option>)}</select></label>
                <label><span>Directness</span><select onChange={(event) => updateRelationship(index, { directness: event.target.value as RelationshipDraft["directness"] })} value={relationship.directness}><option value="direct">Direct code dependency</option><option value="transitive">Transitive dependency</option><option value="conceptual">Architecture ownership</option></select></label>
                <label><span>Verification</span><select onChange={(event) => updateRelationship(index, { verification: { ...relationship.verification, method: event.target.value as RelationshipDraft["verification"]["method"] } })} value={relationship.verification.method}><option value="callsite">Call site</option><option value="import">Import</option><option value="inheritance">Inheritance</option><option value="python_ast">Python reference</option><option value="protobuf_descriptor">Protobuf descriptor</option><option value="test_reference">Test reference</option><option value="manual">Manual evidence</option></select></label>
                <label><span>Stable relationship ID</span><input onChange={(event) => updateRelationship(index, { relationship_id: event.target.value })} required value={relationship.relationship_id} /></label>
              </div>
            </article>)}
          </section>
          {(graph?.relationships.length || graph?.nodes.length) ? <details className="graph-removal-editor"><summary>Remove stale graph records</summary><div><h4>Relationships</h4>{graph?.relationships.map((relationship) => <label key={relationship.relationship_id}><input checked={removeRelationshipIds.includes(relationship.relationship_id)} onChange={(event) => setRemoveRelationshipIds((current) => event.target.checked ? [...current, relationship.relationship_id] : current.filter((value) => value !== relationship.relationship_id))} type="checkbox" />{relationship.relationship_id}</label>)}<h4>Nodes</h4>{graph?.nodes.map((node) => <label key={node.node_id}><input checked={removeNodeIds.includes(node.node_id)} onChange={(event) => setRemoveNodeIds((current) => event.target.checked ? [...current, node.node_id] : current.filter((value) => value !== node.node_id))} type="checkbox" />{node.node_id}</label>)}</div></details> : null}
        </>}
        <div className="modal-actions"><button className="button button-ghost" disabled={saving} onClick={closeReview} type="button">Cancel</button><button className="button button-primary" disabled={saving || !selected.merge_commit_sha}>{saving ? "Preparing proposal…" : "Review exact proposal"}</button></div>
      </form>
    </Modal>
  );
}
