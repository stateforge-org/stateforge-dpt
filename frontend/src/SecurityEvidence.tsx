import {
  AlertTriangle,
  CheckCircle2,
  CircleDot,
  ExternalLink,
  PackageSearch,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "./api";
import type {
  ProjectSecurity,
  Repository,
  RepositorySecurityDetails,
  SecurityFinding,
  SecurityState,
} from "./types";
import { ErrorBanner, Loading, PageHeader } from "./ui";

function timeAgo(value: string | null) {
  if (!value) return "Not assessed";
  const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

const stateLabels: Record<SecurityState, string> = {
  no_known_findings: "No known findings",
  findings: "Findings",
  pending: "Pending",
  stale: "Stale",
  partial: "Partial",
  not_configured: "Not configured",
  unavailable: "Unavailable",
};

function stateIcon(state: SecurityState) {
  if (state === "no_known_findings") return <CheckCircle2 />;
  if (state === "findings") return <ShieldAlert />;
  if (state === "pending") return <RefreshCw />;
  if (state === "partial" || state === "stale") return <AlertTriangle />;
  return <CircleDot />;
}

export function RepositorySecurityStrip({
  repositories,
  onOpen,
}: {
  repositories: Repository[];
  onOpen: (repositoryId?: string) => void;
}) {
  const branch = repositories[0]?.security.branch || "mvp/implementation";
  return (
    <section className="repository-security-strip">
      <header className="repository-security-strip-header">
        <span><ShieldCheck /><strong>Security Status</strong><code>{branch}</code></span>
        <button onClick={() => onOpen()} type="button">View vulnerabilities</button>
      </header>
      <div className="repository-security-cards">
        {repositories.map((repository) => {
          const evidence = repository.security;
          const findings = evidence.dependency_findings + evidence.codeql_findings;
          const detail = evidence.state === "findings"
            ? `${evidence.dependency_findings} dependency · ${evidence.codeql_findings} CodeQL`
            : evidence.message || `${timeAgo(evidence.refreshed_at)} · ${evidence.observed_commit_sha?.slice(0, 7) || "head unknown"}`;
          return (
            <button
              className={`repository-security-card repository-security-card-${evidence.state} repository-security-card-${evidence.highest_severity}`}
              key={repository.repository_id}
              onClick={() => onOpen(repository.repository_id)}
              type="button"
            >
              <span className="repository-security-card-icon">{stateIcon(evidence.state)}</span>
              <span><strong>{repository.alias}</strong><small>{detail}</small></span>
              <b>{evidence.state === "findings" ? `${findings} open` : stateLabels[evidence.state]}</b>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function FindingTable({ findings, empty }: { findings: SecurityFinding[]; empty: string }) {
  if (!findings.length) return <div className="security-empty"><ShieldCheck /><strong>{empty}</strong><span>No matching open findings are present in the current evidence.</span></div>;
  return (
    <div className="security-table-wrap">
      <table className="security-table">
        <thead><tr><th>Severity</th><th>Finding</th><th>Affected source</th><th>Fixed in</th><th>Evidence</th></tr></thead>
        <tbody>
          {findings.map((finding, index) => (
            <tr key={`${finding.id}-${finding.component?.purl || finding.location?.path || index}`}>
              <td><span className={`security-severity security-severity-${finding.severity}`}>{finding.severity}</span></td>
              <td><strong>{finding.summary}</strong><small>{[finding.id, ...(finding.aliases || [])].filter((value, position, values) => values.indexOf(value) === position).join(" · ")}</small></td>
              <td>{finding.component ? <><code>{finding.component.name}</code><small>{finding.component.version}</small></> : <><code>{finding.location?.path || "Repository"}</code><small>{finding.location?.start_line ? `Line ${finding.location.start_line}` : "CodeQL"}</small></>}</td>
              <td>{finding.fixed_versions?.length ? <code>{finding.fixed_versions.join(", ")}</code> : <span>—</span>}</td>
              <td>{finding.source_url ? <a href={finding.source_url} rel="noreferrer" target="_blank">Open <ExternalLink /></a> : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function VulnerabilitiesPage({ projectId }: { projectId: string }) {
  const initialRepository = new URLSearchParams(window.location.hash.split("?")[1] || "").get("repository");
  const [data, setData] = useState<ProjectSecurity | null>(null);
  const [selected, setSelected] = useState<string | null>(initialRepository);
  const [tab, setTab] = useState<"dependency" | "codeql" | "inventory">("dependency");
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    const value = await api<ProjectSecurity>(`/api/projects/${projectId}/security`);
    setData(value);
    if (!selected && value.repositories.length) setSelected(value.repositories[0].repository_id);
    return value;
  }, [projectId, selected]);

  useEffect(() => {
    load().catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "Security evidence is unavailable"));
  }, [load]);

  useEffect(() => {
    if (!data || data.refresh.state !== "running") return;
    const timer = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(timer);
  }, [data, load]);

  const repository = useMemo(
    () => data?.repositories.find((item) => item.repository_id === selected) || data?.repositories[0] || null,
    [data, selected],
  );

  async function refresh() {
    setRefreshing(true);
    setError(null);
    try {
      await api(`/api/projects/${projectId}/security/refresh`, { method: "POST" });
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Security refresh failed");
    } finally {
      setRefreshing(false);
    }
  }

  if (!data) return error ? <ErrorBanner message={error} /> : <Loading label="Loading vulnerability evidence" />;
  const counts = data.summary.severity_counts;
  return (
    <>
      <PageHeader eyebrow="Security evidence" title="Vulnerabilities" description="Dependency and CodeQL findings tied to each repository's exact plan-source commit." actions={<button className="button button-primary" disabled={refreshing || data.refresh.state === "running"} onClick={refresh}><RefreshCw className={data.refresh.state === "running" ? "spin-icon" : undefined} />{data.refresh.state === "running" ? "Assessing…" : "Refresh evidence"}</button>} />
      {error && <ErrorBanner message={error} />}
      <section className="security-summary-grid">
        <article><span>Repositories assessed</span><strong>{data.summary.fully_assessed}<small> of {data.summary.repositories}</small></strong><em>{data.summary.with_findings} with findings</em></article>
        {(["critical", "high", "medium", "low", "unknown"] as const).map((severity) => <article key={severity}><span>{severity}</span><strong className={`security-count-${severity}`}>{counts[severity] || 0}</strong><em>Open findings</em></article>)}
      </section>
      <section className="security-repository-panel">
        <header><div><h2>Repository status</h2><p>Evidence is valid only for the displayed source commit.</p></div><small>{data.refresh.state === "running" ? "Refresh in progress" : "Stored local evidence"}</small></header>
        <div className="security-repository-grid">
          {data.repositories.map((item) => <button className={`${item.repository_id === repository?.repository_id ? "selected " : ""}security-repository-card security-repository-card-${item.security.state}`} key={item.repository_id} onClick={() => setSelected(item.repository_id)} type="button"><span>{stateIcon(item.security.state)}</span><strong>{item.alias}</strong><b>{stateLabels[item.security.state]}</b><small>{item.security.observed_commit_sha?.slice(0, 7) || "Head not assessed"}</small></button>)}
        </div>
      </section>
      {repository && <RepositoryEvidence repository={repository} tab={tab} onTab={setTab} />}
    </>
  );
}

function RepositoryEvidence({ repository, tab, onTab }: { repository: RepositorySecurityDetails; tab: "dependency" | "codeql" | "inventory"; onTab: (tab: "dependency" | "codeql" | "inventory") => void }) {
  const evidence = repository.security;
  return (
    <>
      <section className={`security-evidence-banner security-evidence-banner-${evidence.state} security-evidence-banner-${evidence.highest_severity}`}>
        {stateIcon(evidence.state)}<div><strong>{repository.alias}: {stateLabels[evidence.state]}</strong><p>{evidence.message || `Assessment recorded ${timeAgo(evidence.refreshed_at)} for ${evidence.observed_commit_sha?.slice(0, 12) || "an unresolved head"}.`}</p></div><dl><div><dt>Branch</dt><dd><code>{evidence.branch}</code></dd></div><div><dt>Dependencies</dt><dd>{evidence.dependency_findings} findings</dd></div><div><dt>CodeQL</dt><dd>{evidence.codeql_findings} alerts</dd></div></dl>
      </section>
      <section className="security-findings-panel">
        <header><div><h2>Findings</h2><p>Open findings grouped by evidence source.</p></div><div className="security-tabs"><button className={tab === "dependency" ? "active" : ""} onClick={() => onTab("dependency")} type="button">Dependencies <span>{repository.dependency_findings.length}</span></button><button className={tab === "codeql" ? "active" : ""} onClick={() => onTab("codeql")} type="button">CodeQL <span>{repository.codeql_findings.length}</span></button><button className={tab === "inventory" ? "active" : ""} onClick={() => onTab("inventory")} type="button">SBOM inventory</button></div></header>
        {tab === "dependency" && <FindingTable findings={repository.dependency_findings} empty="No known dependency findings" />}
        {tab === "codeql" && <FindingTable findings={repository.codeql_findings} empty="No open CodeQL alerts" />}
        {tab === "inventory" && <SbomInventory repository={repository} />}
      </section>
      <section className="security-provenance"><div><PackageSearch /><span><strong>Evidence provenance</strong><small>The immutable inputs behind this status.</small></span></div><dl><div><dt>Source commit</dt><dd><code>{evidence.observed_commit_sha?.slice(0, 12) || "Unavailable"}</code></dd></div><div><dt>SBOM</dt><dd>{repository.sbom ? `${repository.sbom.format} ${repository.sbom.spec_version}` : "Unavailable"}</dd></div><div><dt>Dependency material</dt><dd><code>{repository.sbom?.dependency_material_digest.slice(0, 12) || "Unavailable"}</code></dd></div><div><dt>Generator</dt><dd>{repository.sbom ? `${repository.sbom.adapter} v${repository.sbom.adapter_version} · ${repository.sbom.tool} ${repository.sbom.tool_version}${repository.sbom.generator.duration_ms !== undefined ? ` · ${repository.sbom.generator.duration_ms} ms` : ""}` : "Unavailable"}</dd></div><div><dt>OSV evidence</dt><dd>{evidence.dependency_status.state.replaceAll("_", " ")}</dd></div><div><dt>CodeQL evidence</dt><dd>{evidence.codeql_status.state.replaceAll("_", " ")}</dd></div></dl></section>
    </>
  );
}

function SbomInventory({ repository }: { repository: RepositorySecurityDetails }) {
  if (!repository.sbom) return <div className="security-empty"><PackageSearch /><strong>No supported SBOM</strong><span>This repository has no validated dependency inventory yet.</span></div>;
  const total = repository.sbom.component_count;
  const queryable = repository.sbom.queryable_component_count;
  const percent = total ? Math.round((queryable / total) * 100) : 0;
  return <div className="security-inventory"><div><span><strong>{total}</strong><small>Total components</small></span><span><strong>{queryable}</strong><small>Queryable components</small></span><span><strong>{percent}%</strong><small>OSV coverage</small></span></div><div className="security-coverage" role="progressbar" aria-label="Queryable SBOM component coverage" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><i style={{ width: `${percent}%` }} /></div><p>{queryable === total ? "Every component has a versioned Package URL." : `${total - queryable} components are visible but cannot be queried reliably.`}</p></div>;
}
