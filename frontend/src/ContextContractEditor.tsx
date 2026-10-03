import {
  ArrowDown,
  ArrowUp,
  BookOpenCheck,
  Check,
  ChevronDown,
  ChevronRight,
  FileCode2,
  Folder,
  FolderSearch2,
  Plus,
  Search,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";

import { api } from "./api";
import type {
  ContextAuthority,
  ContextResource,
  ContextRole,
  ContextSelectorKind,
  Dashboard,
  Repository,
  VerificationRequirement,
} from "./types";

const SourceCodePreview = lazy(() =>
  import("./SourceCodePreview").then((module) => ({ default: module.SourceCodePreview })),
);

const roleOptions: Array<{ value: ContextRole; title: string; copy: string }> = [
  { value: "must_understand", title: "Must understand", copy: "Required to reason about the change." },
  { value: "expected_to_change", title: "Expected to change", copy: "Defines the intended PR change surface." },
  { value: "must_verify", title: "Must verify", copy: "Must be inspected when proving correctness." },
  { value: "must_preserve", title: "Must preserve", copy: "Behavior or structure this work cannot break." },
];

const authorityOptions: Array<{ value: ContextAuthority; label: string }> = [
  { value: "canonical", label: "Canonical" },
  { value: "reference", label: "Reference example" },
  { value: "generated", label: "Generated — do not edit" },
  { value: "compatibility", label: "Compatibility-only" },
  { value: "deprecated", label: "Deprecated" },
  { value: "historical", label: "Historical — do not imitate" },
];

const selectorOptions: Array<{ value: ContextSelectorKind; label: string }> = [
  { value: "file", label: "Whole file" },
  { value: "directory", label: "Directory" },
  { value: "glob", label: "File pattern" },
  { value: "symbol", label: "Symbol" },
  { value: "protobuf", label: "Protobuf declaration" },
  { value: "json_schema", label: "JSON Schema definition" },
  { value: "markdown_heading", label: "Markdown heading" },
  { value: "test_case", label: "Test case" },
];

type RepositoryPreview = {
  content: string | null;
  binary: boolean;
  truncated: boolean;
  selectors: Array<{ kind: ContextSelectorKind; value: string; label: string }>;
};

type RepositoryTreeNode = {
  name: string;
  path: string;
  kind: "directory" | "file";
  children: RepositoryTreeNode[];
};

type MutableRepositoryTreeNode = Omit<RepositoryTreeNode, "children"> & {
  children: Map<string, MutableRepositoryTreeNode>;
};

function buildRepositoryTree(paths: string[]): RepositoryTreeNode[] {
  const root = new Map<string, MutableRepositoryTreeNode>();
  for (const path of paths) {
    const parts = path.split("/").filter(Boolean);
    let children = root;
    let currentPath = "";
    parts.forEach((part, index) => {
      currentPath = currentPath ? `${currentPath}/${part}` : part;
      const kind = index === parts.length - 1 ? "file" : "directory";
      let node = children.get(part);
      if (!node) {
        node = { name: part, path: currentPath, kind, children: new Map() };
        children.set(part, node);
      }
      children = node.children;
    });
  }

  function materialize(
    children: Map<string, MutableRepositoryTreeNode>,
  ): RepositoryTreeNode[] {
    return [...children.values()]
      .sort((left, right) => {
        if (left.kind !== right.kind) return left.kind === "directory" ? -1 : 1;
        return left.name.localeCompare(right.name, undefined, { sensitivity: "base" });
      })
      .map((node) => ({
        name: node.name,
        path: node.path,
        kind: node.kind,
        children: materialize(node.children),
      }));
  }

  return materialize(root);
}

function localId(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

export function ContextContractEditor({
  dashboard,
  resources,
  setResources,
  verification,
  setVerification,
}: {
  dashboard: Dashboard;
  resources: ContextResource[];
  setResources: (resources: ContextResource[]) => void;
  verification: VerificationRequirement[];
  setVerification: (requirements: VerificationRequirement[]) => void;
}) {
  const [browserFor, setBrowserFor] = useState<string | null>(null);

  function addResource() {
    const repository = dashboard.repositories[0];
    if (!repository) return;
    const contextId = localId("context");
    setResources([
      ...resources,
      {
        context_id: contextId,
        repository_id: repository.repository_id,
        repository_alias: repository.alias,
        github_full_name: repository.github_full_name,
        repository_local_path: repository.local_path,
        path: "",
        selector_kind: "file",
        selector_value: null,
        role: "must_understand",
        explanation: "",
        required: true,
        authority: "canonical",
        observation: null,
      },
    ]);
    setBrowserFor(contextId);
  }

  function updateResource(contextId: string, patch: Partial<ContextResource>) {
    setResources(
      resources.map((resource) =>
        resource.context_id === contextId ? { ...resource, ...patch } : resource,
      ),
    );
  }

  function moveResource(index: number, direction: -1 | 1) {
    const destination = index + direction;
    if (destination < 0 || destination >= resources.length) return;
    const reordered = [...resources];
    [reordered[index], reordered[destination]] = [reordered[destination], reordered[index]];
    setResources(reordered);
  }

  function moveVerification(index: number, direction: -1 | 1) {
    const destination = index + direction;
    if (destination < 0 || destination >= verification.length) return;
    const reordered = [...verification];
    [reordered[index], reordered[destination]] = [reordered[destination], reordered[index]];
    setVerification(reordered);
  }

  function addVerification() {
    setVerification([
      ...verification,
      {
        verification_id: localId("verify"),
        kind: "test",
        repository_id: dashboard.repositories[0]?.repository_id || null,
        repository_alias: dashboard.repositories[0]?.alias || null,
        target: "",
        explanation: "",
        required: true,
        observation: null,
      },
    ]);
  }

  return (
    <section className="context-contract-editor">
      <header className="context-contract-heading">
        <div>
          <span className="context-step-number">4</span>
          <div>
            <strong>What must the implementer see, change, prove, and preserve?</strong>
            <small>Choose repository resources visually. These links travel with the Git plan.</small>
          </div>
        </div>
      </header>

      <div className="context-role-legend">
        {roleOptions.map((role) => (
          <span className={`context-role context-role-${role.value}`} key={role.value}>
            <strong>{role.title}</strong><small>{role.copy}</small>
          </span>
        ))}
      </div>

      <div className="context-resource-list">
        {resources.map((resource, index) => (
          <article className={`context-resource-card context-role-border-${resource.role}`} key={resource.context_id}>
            <header>
              <span><FileCode2 /><strong>Context {index + 1}</strong></span>
              <div className="context-order-actions">
                <button aria-label="Move context resource up" disabled={index === 0} onClick={() => moveResource(index, -1)} type="button"><ArrowUp /></button>
                <button aria-label="Move context resource down" disabled={index === resources.length - 1} onClick={() => moveResource(index, 1)} type="button"><ArrowDown /></button>
                <button aria-label="Remove context resource" onClick={() => setResources(resources.filter((item) => item.context_id !== resource.context_id))} type="button"><Trash2 /></button>
              </div>
            </header>
            <div className="context-resource-grid">
              <label>
                <span>Repository</span>
                <select
                  value={resource.repository_id}
                  onChange={(event) => {
                    const repository = dashboard.repositories.find((item) => item.repository_id === event.target.value);
                    if (repository) updateResource(resource.context_id, {
                      repository_id: repository.repository_id,
                      repository_alias: repository.alias,
                      github_full_name: repository.github_full_name,
                      repository_local_path: repository.local_path,
                      path: "",
                      observation: null,
                    });
                  }}
                >
                  {dashboard.repositories.map((repository) => <option key={repository.repository_id} value={repository.repository_id}>{repository.alias}</option>)}
                </select>
              </label>
              <label className="context-path-field">
                <span>Repository path</span>
                <button className="context-path-picker" onClick={() => setBrowserFor(resource.context_id)} type="button">
                  <FolderSearch2 />
                  <span>{resource.path || "Choose a tracked file or directory"}</span>
                  <ChevronRight />
                </button>
              </label>
              <label>
                <span>Context role</span>
                <select value={resource.role} onChange={(event) => updateResource(resource.context_id, { role: event.target.value as ContextRole })}>
                  {roleOptions.map((role) => <option key={role.value} value={role.value}>{role.title}</option>)}
                </select>
              </label>
              <label>
                <span>Authority</span>
                <select value={resource.authority} onChange={(event) => updateResource(resource.context_id, { authority: event.target.value as ContextAuthority })}>
                  {authorityOptions.map((authority) => <option key={authority.value} value={authority.value}>{authority.label}</option>)}
                </select>
              </label>
              <label>
                <span>Selection</span>
                <select value={resource.selector_kind} onChange={(event) => updateResource(resource.context_id, { selector_kind: event.target.value as ContextSelectorKind, selector_value: null })}>
                  {selectorOptions.map((selector) => <option key={selector.value} value={selector.value}>{selector.label}</option>)}
                </select>
              </label>
              {!(["file", "directory", "glob"] as string[]).includes(resource.selector_kind) && (
                <label>
                  <span>Selector name</span>
                  <input onChange={(event) => updateResource(resource.context_id, { selector_value: event.target.value })} placeholder="Exact symbol, message, heading, or test" value={resource.selector_value || ""} />
                </label>
              )}
              <label className="context-explanation-field">
                <span>Why does this matter?</span>
                <textarea onChange={(event) => updateResource(resource.context_id, { explanation: event.target.value })} placeholder="Explain the decision, invariant, or expected effect this resource carries." rows={2} value={resource.explanation} />
              </label>
            </div>
            <footer>
              <label><input checked={resource.required} onChange={(event) => updateResource(resource.context_id, { required: event.target.checked })} type="checkbox" />Required before implementation</label>
              {resource.observation && <span className={`context-observation context-observation-${resource.observation.status}`}><ShieldCheck />{resource.observation.status.replaceAll("_", " ")}</span>}
            </footer>
          </article>
        ))}
        <button className="context-add-card" onClick={addResource} type="button"><Plus /><strong>Add context resource</strong><span>Browse any managed repository</span></button>
      </div>

      <div className="verification-editor">
        <header><BookOpenCheck /><div><strong>How will a human know this is correct?</strong><small>Define concrete evidence before implementation begins.</small></div><button className="button button-secondary button-small" onClick={addVerification} type="button"><Plus />Add evidence</button></header>
        {verification.map((requirement, index) => (
          <div className="verification-row" key={requirement.verification_id}>
            <select value={requirement.kind} onChange={(event) => setVerification(verification.map((item) => item.verification_id === requirement.verification_id ? { ...item, kind: event.target.value as VerificationRequirement["kind"] } : item))}>
              <option value="test">Test</option><option value="command">Command</option><option value="schema">Schema</option><option value="generated">Generated output</option><option value="typecheck">Type check</option><option value="lint">Lint</option><option value="integration">Integration</option><option value="human">Human review</option>
            </select>
            <select value={requirement.repository_id || ""} onChange={(event) => { const repository = dashboard.repositories.find((item) => item.repository_id === event.target.value); setVerification(verification.map((item) => item.verification_id === requirement.verification_id ? { ...item, repository_id: repository?.repository_id || null, repository_alias: repository?.alias || null } : item)); }}>
              <option value="">Whole project</option>{dashboard.repositories.map((repository) => <option key={repository.repository_id} value={repository.repository_id}>{repository.alias}</option>)}
            </select>
            <input onChange={(event) => setVerification(verification.map((item) => item.verification_id === requirement.verification_id ? { ...item, target: event.target.value } : item))} placeholder="tests/test_contract.py or npm test" value={requirement.target} />
            <input onChange={(event) => setVerification(verification.map((item) => item.verification_id === requirement.verification_id ? { ...item, explanation: event.target.value } : item))} placeholder="What this evidence proves" value={requirement.explanation} />
            <div className="verification-order-actions">
              <button aria-label="Move verification up" disabled={index === 0} onClick={() => moveVerification(index, -1)} type="button"><ArrowUp /></button>
              <button aria-label="Move verification down" disabled={index === verification.length - 1} onClick={() => moveVerification(index, 1)} type="button"><ArrowDown /></button>
              <button aria-label="Remove verification requirement" onClick={() => setVerification(verification.filter((item) => item.verification_id !== requirement.verification_id))} type="button"><Trash2 /></button>
            </div>
          </div>
        ))}
        {!verification.length && <p>No proof has been defined. Add at least one required check before implementation can start.</p>}
      </div>

      {browserFor && (() => {
        const resource = resources.find((item) => item.context_id === browserFor);
        return resource ? <RepositoryBrowser repositories={dashboard.repositories} initialRepositoryId={resource.repository_id} onChoose={(repository, path, selectorKind = "file", selectorValue = null) => { updateResource(resource.context_id, { repository_id: repository.repository_id, repository_alias: repository.alias, github_full_name: repository.github_full_name, repository_local_path: repository.local_path, path, selector_kind: selectorKind, selector_value: selectorValue, observation: null }); setBrowserFor(null); }} onClose={() => setBrowserFor(null)} /> : null;
      })()}

    </section>
  );
}

function RepositoryBrowser({ repositories, initialRepositoryId, onChoose, onClose }: { repositories: Repository[]; initialRepositoryId: string; onChoose: (repository: Repository, path: string, selectorKind?: ContextSelectorKind, selectorValue?: string | null) => void; onClose: () => void }) {
  const [activeRepositoryId, setActiveRepositoryId] = useState(initialRepositoryId);
  const [query, setQuery] = useState("");
  const [paths, setPaths] = useState<string[]>([]);
  const [totalPaths, setTotalPaths] = useState(0);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [preview, setPreview] = useState<RepositoryPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const activeRepository = repositories.find((repository) => repository.repository_id === activeRepositoryId) || repositories[0];
  const tree = useMemo(() => buildRepositoryTree(paths), [paths]);
  const requestPath = useMemo(() => activeRepository ? `/api/repositories/${activeRepository.repository_id}/tree?query=${encodeURIComponent(query)}&limit=500` : null, [activeRepository, query]);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  useEffect(() => {
    if (!requestPath) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setLoading(true);
      api<{ paths: string[]; directories: string[]; total: number }>(requestPath).then((result) => { if (!cancelled) { setPaths(result.paths); setTotalPaths(result.total); setExpanded(query ? new Set(result.directories) : new Set()); setError(null); } }).catch((caught: unknown) => { if (!cancelled) { setPaths([]); setTotalPaths(0); setError(caught instanceof Error ? caught.message : "Repository files could not be loaded"); } }).finally(() => { if (!cancelled) setLoading(false); });
    }, 180);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [requestPath]);

  async function selectFile(path: string) {
    if (!activeRepository) return;
    setSelectedPath(path);
    setPreviewLoading(true);
    setError(null);
    try {
      const result = await api<RepositoryPreview>(`/api/repositories/${activeRepository.repository_id}/resource-preview?path=${encodeURIComponent(path)}`);
      setPreview(result);
    } catch (caught) {
      setPreview(null);
      setError(caught instanceof Error ? caught.message : "File preview could not be loaded");
    } finally {
      setPreviewLoading(false);
    }
  }

  function switchRepository(repositoryId: string) {
    if (repositoryId === activeRepositoryId) return;
    setActiveRepositoryId(repositoryId);
    setQuery("");
    setPaths([]);
    setTotalPaths(0);
    setExpanded(new Set());
    setSelectedPath(null);
    setPreview(null);
    setError(null);
  }

  function toggleDirectory(path: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  if (!activeRepository) return null;
  return (
    <div className="repository-browser-backdrop" onMouseDown={onClose} onWheel={(event) => event.stopPropagation()}>
      <section aria-label="Repository file selector" aria-modal="true" onMouseDown={(event) => event.stopPropagation()} role="dialog">
        <header><FolderSearch2 /><div><strong>Choose repository context</strong><small>Only Git-tracked files are shown.</small><nav aria-label="Select repository" className="repository-browser-repositories">{repositories.map((repository) => <button aria-pressed={repository.repository_id === activeRepository.repository_id} className={repository.repository_id === activeRepository.repository_id ? "active" : ""} disabled={!repository.local_path} key={repository.repository_id} onClick={() => switchRepository(repository.repository_id)} title={repository.local_path ? repository.github_full_name : "Attach a local clone before browsing"} type="button">{repository.alias}</button>)}</nav></div><button onClick={onClose} type="button">Close</button></header>
        <label><Search /><input autoFocus onChange={(event) => setQuery(event.target.value)} placeholder="Search paths" value={query} /></label>
        <div className="repository-browser-body">
          <div className="repository-browser-list">
            <div className="repository-tree-heading"><span><Folder />{activeRepository.alias}</span><small>{loading ? "Reading index…" : `${totalPaths} tracked ${totalPaths === 1 ? "file" : "files"}`}</small></div>
            {error ? <p className="repository-browser-error">{error}</p> : loading ? <p>Reading repository index…</p> : tree.length ? <RepositoryTree nodes={tree} expanded={expanded} selectedPath={selectedPath} onChooseDirectory={(path) => onChoose(activeRepository, path, "directory", null)} onSelectFile={selectFile} onToggleDirectory={toggleDirectory} /> : <p>No tracked files match this search.</p>}
            {!loading && !query && totalPaths > paths.length && <p className="repository-tree-limit">Showing the first {paths.length} files. Search to find paths outside this view.</p>}
          </div>
          <aside className="repository-browser-preview">
            {!selectedPath ? <div className="repository-preview-empty"><FileCode2 /><strong>Select a file to preview it</strong><span>Then choose the whole file or a detected declaration.</span></div> : previewLoading ? <p>Reading {selectedPath}…</p> : preview ? (
              <>
                <header><div><strong>{selectedPath}</strong><small>{preview.binary ? "Binary file" : preview.truncated ? "Preview truncated to 128 KiB" : "Tracked text file"}</small></div><button className="button button-primary button-small" onClick={() => onChoose(activeRepository, selectedPath, "file", null)} type="button"><Check />Use whole file</button></header>
                {preview.selectors.length > 0 && <div className="repository-selector-list"><span>Or choose a declaration</span>{preview.selectors.map((selector) => <button key={`${selector.kind}:${selector.value}`} onClick={() => onChoose(activeRepository, selectedPath, selector.kind, selector.value)} type="button"><code>{selector.kind.replaceAll("_", " ")}</code><strong>{selector.label}</strong><ChevronRight /></button>)}</div>}
                {preview.content !== null ? <Suspense fallback={<p>Preparing highlighted preview…</p>}><SourceCodePreview content={preview.content} path={selectedPath} /></Suspense> : <div className="repository-preview-empty"><FileCode2 /><strong>Preview unavailable</strong><span>You can still select this tracked file as a whole.</span></div>}
              </>
            ) : null}
          </aside>
        </div>
      </section>
    </div>
  );
}

function RepositoryTree({ nodes, expanded, selectedPath, onToggleDirectory, onChooseDirectory, onSelectFile, depth = 0 }: { nodes: RepositoryTreeNode[]; expanded: Set<string>; selectedPath: string | null; onToggleDirectory: (path: string) => void; onChooseDirectory: (path: string) => void; onSelectFile: (path: string) => void; depth?: number }) {
  return <div className={depth === 0 ? "repository-tree" : "repository-tree-children"}>{nodes.map((node) => node.kind === "directory" ? (
    <div className="repository-tree-node" key={node.path}>
      <div className="repository-tree-directory" style={{ paddingLeft: `${8 + depth * 16}px` }}>
        <button aria-expanded={expanded.has(node.path)} className="repository-tree-toggle" onClick={() => onToggleDirectory(node.path)} type="button">{expanded.has(node.path) ? <ChevronDown /> : <ChevronRight />}<Folder /><span>{node.name}</span></button>
        <button className="repository-tree-choose-directory" onClick={() => onChooseDirectory(node.path)} type="button">Use folder</button>
      </div>
      {expanded.has(node.path) && <RepositoryTree depth={depth + 1} expanded={expanded} nodes={node.children} onChooseDirectory={onChooseDirectory} onSelectFile={onSelectFile} onToggleDirectory={onToggleDirectory} selectedPath={selectedPath} />}
    </div>
  ) : (
    <button className={`repository-tree-file${selectedPath === node.path ? " selected" : ""}`} key={node.path} onClick={() => onSelectFile(node.path)} style={{ paddingLeft: `${36 + depth * 16}px` }} title={node.path} type="button"><FileCode2 /><span>{node.name}</span><ChevronRight /></button>
  ))}</div>;
}
