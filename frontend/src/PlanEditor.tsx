import {
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronRight,
  CircleDot,
  FileCode2,
  FolderGit2,
  GitBranch,
  Layers3,
  Pencil,
  Plus,
  Route,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
  WandSparkles,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { api } from "./api";
import type { Dashboard, PlanSource, PlanSyncStatus, Repository, Step } from "./types";
import {
  EmptyState,
  ErrorBanner,
  Modal,
  PageHeader,
  SearchInput,
  StatusBadge,
} from "./ui";

type EditorTarget = Step | "new" | null;

export function PlanEditorPage({
  dashboard,
  onChanged,
  onGenerate,
  onOpenDelivery,
}: {
  dashboard: Dashboard;
  onChanged: () => Promise<void>;
  onGenerate: () => Promise<boolean>;
  onOpenDelivery: (step: Step) => void;
}) {
  const [editorTarget, setEditorTarget] = useState<EditorTarget>(null);
  const [query, setQuery] = useState("");
  const [repositoryFilter, setRepositoryFilter] = useState("all");
  const [preview, setPreview] = useState<string | null>(null);
  const [publishOpen, setPublishOpen] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [publishing, setPublishing] = useState(false);
  const [syncStatus, setSyncStatus] = useState<PlanSyncStatus | null>(null);
  const [syncLoading, setSyncLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [resolveOpen, setResolveOpen] = useState(false);
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null);

  const loadSyncStatus = useCallback(async () => {
    setSyncLoading(true);
    try {
      const status = await api<PlanSyncStatus>(
        `/api/projects/${dashboard.project.project_id}/plan/sources`,
      );
      setSyncStatus(status);
      setSyncError(null);
    } catch (caught) {
      setSyncError(caught instanceof Error ? caught.message : "Git plan status could not be loaded");
    } finally {
      setSyncLoading(false);
    }
  }, [dashboard.project.project_id]);

  useEffect(() => {
    void loadSyncStatus();
  }, [dashboard, loadSyncStatus]);

  async function importPlan(repositoryId: string, force = false) {
    setSyncing(true);
    setSyncError(null);
    try {
      await api(`/api/projects/${dashboard.project.project_id}/plan/import`, {
        method: "POST",
        body: JSON.stringify({ repository_id: repositoryId, force }),
      });
      setResolveOpen(false);
      await onChanged();
      await loadSyncStatus();
    } catch (caught) {
      setSyncError(caught instanceof Error ? caught.message : "The Git plan could not be imported");
    } finally {
      setSyncing(false);
    }
  }

  const visibleSteps = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return dashboard.steps.filter((step) => {
      const repositoryMatches =
        repositoryFilter === "all" || step.repository_id === repositoryFilter;
      const queryMatches =
        !needle ||
        `${step.step_id} ${step.title} ${step.required_result} ${step.repository_alias}`
          .toLowerCase()
          .includes(needle);
      return repositoryMatches && queryMatches;
    });
  }, [dashboard.steps, query, repositoryFilter]);

  const visibleIds = new Set(visibleSteps.map((step) => step.step_id));
  const layers = dependencyLayers(dashboard.steps)
    .map((steps, level) => ({
      level,
      steps: steps.filter((step) => visibleIds.has(step.step_id)),
    }))
    .filter((layer) => layer.steps.length > 0);

  async function showPreview(forPublishing = false) {
    setPreviewError(null);
    try {
      const response = await fetch(
        `/api/projects/${dashboard.project.project_id}/plan/preview`,
      );
      if (!response.ok) throw new Error("The generated plan preview could not be loaded");
      setPreview(await response.text());
      setPublishOpen(forPublishing);
    } catch (caught) {
      setPreviewError(
        caught instanceof Error ? caught.message : "The plan preview could not be loaded",
      );
    }
  }

  async function publish() {
    setPublishing(true);
    try {
      const published = await onGenerate();
      if (published) {
        setPreview(null);
        setPublishOpen(false);
      }
    } finally {
      setPublishing(false);
    }
  }

  const selectedStep =
    editorTarget && editorTarget !== "new" ? editorTarget : null;
  const attachedRepositories = dashboard.repositories.filter(
    (repository) => repository.local_path,
  );

  return (
    <>
      <PageHeader
        eyebrow="Visual plan builder"
        title="Plan editor"
        description="Build the roadmap by choosing visible steps and connections. DPT handles IDs, ordering, and dependency validation."
        actions={(
          <>
            <button className="button button-secondary" onClick={() => showPreview()}>
              <FileCode2 />Preview output
            </button>
            <button className="button button-primary" onClick={() => showPreview(true)}>
              <WandSparkles />Review & publish
            </button>
          </>
        )}
      />

      {previewError && <ErrorBanner message={previewError} />}

      <GitPlanStatus
        loading={syncLoading}
        onImport={(repositoryId) => importPlan(repositoryId)}
        onResolve={(source) => {
          setSelectedSourceId(source.repository_id);
          setResolveOpen(true);
        }}
        status={syncStatus}
        syncing={syncing}
      />
      {syncError && <ErrorBanner message={syncError} />}

      <section className="plan-builder-summary">
        <div><strong>{dashboard.steps.length}</strong><span>steps</span></div>
        <div><strong>{dashboard.repositories.length}</strong><span>repositories</span></div>
        <div><strong>{dashboard.project.revision}</strong><span>published revision</span></div>
        <div className="plan-builder-summary-copy">
          <Sparkles />
          <span><strong>Click any card to edit it</strong><small>Connections determine when work becomes available.</small></span>
        </div>
        <button
          className="button button-primary"
          disabled={!dashboard.repositories.length}
          onClick={() => setEditorTarget("new")}
        >
          <Plus />Create a step
        </button>
      </section>

      <section className="plan-builder-toolbar">
        <SearchInput value={query} onChange={setQuery} placeholder="Find a step by name or outcome" />
        <div className="plan-builder-repositories" aria-label="Filter by repository">
          <button
            className={repositoryFilter === "all" ? "active" : ""}
            onClick={() => setRepositoryFilter("all")}
            type="button"
          >
            All repositories
          </button>
          {dashboard.repositories.map((repository) => (
            <button
              className={repositoryFilter === repository.repository_id ? "active" : ""}
              key={repository.repository_id}
              onClick={() => setRepositoryFilter(repository.repository_id)}
              type="button"
            >
              <i />{repository.alias}
            </button>
          ))}
        </div>
      </section>

      <section className="plan-builder-canvas">
        <header>
          <span><Layers3 />Dependency flow</span>
          <small>Foundation steps start on the left. Every selected prerequisite moves work to the right.</small>
        </header>
        {layers.length ? (
          <div className="plan-builder-lanes">
            {layers.map((layer, index) => (
              <section className="plan-builder-lane" key={layer.level}>
                <header>
                  <span>{String(layer.level + 1).padStart(2, "0")}</span>
                  <div>
                    <strong>{layer.level === 0 ? "Starts here" : `Stage ${layer.level + 1}`}</strong>
                    <small>{layer.level === 0 ? "No prerequisites" : `After stage ${layer.level}`}</small>
                  </div>
                </header>
                <div>
                  {layer.steps.map((step) => (
                    <PlanStepCard
                      dashboard={dashboard}
                      key={step.step_id}
                      onClick={() => setEditorTarget(step)}
                      step={step}
                    />
                  ))}
                </div>
                {index < layers.length - 1 && <ArrowRight className="plan-builder-lane-arrow" />}
              </section>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<Route />}
            title={dashboard.steps.length ? "No steps match" : "Create the first step"}
            description={dashboard.steps.length ? "Clear the search or choose another repository." : "Pick a repository, describe the result, and DPT will place it in the plan."}
            action={!dashboard.steps.length && dashboard.repositories.length ? (
              <button className="button button-primary" onClick={() => setEditorTarget("new")}>
                <Plus />Create the first step
              </button>
            ) : undefined}
          />
        )}
      </section>

      {editorTarget && (
        <StepEditorModal
          dashboard={dashboard}
          key={selectedStep?.step_id || "new-step"}
          onClose={() => setEditorTarget(null)}
          onOpenDelivery={onOpenDelivery}
          onSaved={onChanged}
          step={selectedStep}
        />
      )}

      {preview !== null && (
        <Modal
          description={publishOpen ? "Review exactly what DPT will write before publishing it to every attached repository." : "This rendered document is generated identically in every managed repository."}
          extraWide
          onClose={() => {
            if (publishing) return;
            setPreview(null);
            setPublishOpen(false);
          }}
          title={publishOpen ? `Publish revision ${dashboard.project.revision + 1}?` : `${dashboard.project.generated_filename} preview`}
        >
          {publishOpen && (
            <section className="plan-publish-summary">
              <div><strong>{dashboard.steps.length}</strong><span>steps in this revision</span></div>
              <ArrowRight />
              <div><strong>{attachedRepositories.length}</strong><span>local copies updated</span></div>
              <div className="plan-publish-targets">
                {attachedRepositories.map((repository) => (
                  <span key={repository.repository_id}><FolderGit2 />{repository.alias}</span>
                ))}
              </div>
            </section>
          )}
          <div className="plan-rendered-preview">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{preview}</ReactMarkdown>
          </div>
          <div className="modal-actions">
            <button className="button button-ghost" disabled={publishing} onClick={() => { setPreview(null); setPublishOpen(false); }} type="button">Close</button>
            {publishOpen && (
              <button className="button button-primary" disabled={publishing || !attachedRepositories.length} onClick={publish} type="button">
                <WandSparkles />{publishing ? "Publishing…" : `Publish revision ${dashboard.project.revision + 1}`}
              </button>
            )}
          </div>
        </Modal>
      )}

      {resolveOpen && syncStatus && (
        <PlanConflictModal
          onClose={() => !syncing && setResolveOpen(false)}
          onImport={(repositoryId) => importPlan(repositoryId, true)}
          onSelect={setSelectedSourceId}
          selectedSourceId={selectedSourceId}
          sources={syncStatus.sources}
          syncing={syncing}
        />
      )}
    </>
  );
}

function GitPlanStatus({
  loading,
  onImport,
  onResolve,
  status,
  syncing,
}: {
  loading: boolean;
  onImport: (repositoryId: string) => void;
  onResolve: (source: PlanSource) => void;
  status: PlanSyncStatus | null;
  syncing: boolean;
}) {
  if (loading && !status) {
    return <section className="git-plan-status is-loading"><RefreshCw /><div><strong>Checking Git plan copies…</strong><span>Comparing local milestones with every attached repository.</span></div></section>;
  }
  if (!status) return null;

  const currentCopies = status.sources.filter((source) => source.status === "current");
  const recommended = status.sources.find((source) => source.repository_id === status.recommended_repository_id);
  const conflictSource = status.sources
    .filter((source) => source.fingerprint)
    .sort((left, right) => (right.revision || 0) - (left.revision || 0))[0];

  if (status.conflict) {
    return (
      <section className="git-plan-status is-conflict">
        <AlertTriangle />
        <div>
          <strong>{status.has_local_changes ? "Git changed while this computer has unpublished edits" : "Git plan copies disagree"}</strong>
          <span>DPT stopped before replacing anything. Choose the plan you want to keep after reviewing its revision and repository.</span>
        </div>
        {conflictSource && <button className="button button-secondary" disabled={syncing} onClick={() => onResolve(conflictSource)} type="button">Resolve safely</button>}
      </section>
    );
  }

  if (recommended) {
    return (
      <section className="git-plan-status is-update">
        <RefreshCw />
        <div>
          <strong>Revision {recommended.revision} is available from Git</strong>
          <span>Import the complete plan from {recommended.github_full_name}. No local draft edits will be lost.</span>
        </div>
        <button className="button button-primary" disabled={syncing} onClick={() => onImport(recommended.repository_id)} type="button">
          <RefreshCw />{syncing ? "Importing…" : `Import revision ${recommended.revision}`}
        </button>
      </section>
    );
  }

  if (status.has_local_changes) {
    return (
      <section className="git-plan-status is-draft">
        <GitBranch />
        <div>
          <strong>Unpublished plan edits on this computer</strong>
          <span>Review and publish to write the next revision into every attached clone, then commit and push those files with Git.</span>
        </div>
      </section>
    );
  }

  if (currentCopies.length) {
    return (
      <section className="git-plan-status is-current">
        <ShieldCheck />
        <div>
          <strong>Git plan is current · revision {status.local_revision}</strong>
          <span>{currentCopies.length} verified {currentCopies.length === 1 ? "copy" : "copies"}. Pulling these repositories onto another computer can recreate the same project.</span>
        </div>
        <code>{status.published_fingerprint?.slice(0, 10)}</code>
      </section>
    );
  }

  return (
    <section className="git-plan-status is-unpublished">
      <GitBranch />
      <div>
        <strong>Publish once to make this project portable</strong>
        <span>DPT will embed the complete plan in {"STATEFORGE_DPT.md"}. Commit and push it so another computer can import everything from any repository.</span>
      </div>
    </section>
  );
}

function PlanConflictModal({
  onClose,
  onImport,
  onSelect,
  selectedSourceId,
  sources,
  syncing,
}: {
  onClose: () => void;
  onImport: (repositoryId: string) => void;
  onSelect: (repositoryId: string) => void;
  selectedSourceId: string | null;
  sources: PlanSource[];
  syncing: boolean;
}) {
  const selectable = sources.filter((source) => source.fingerprint && source.revision !== null);
  const selected = selectable.find((source) => source.repository_id === selectedSourceId) || selectable[0];
  return (
    <Modal
      description="DPT will never guess during a plan conflict. Compare the Git copies below, then explicitly choose the one that should replace this computer's working plan."
      onClose={onClose}
      title="Choose the Git plan to keep"
      wide
    >
      <div className="plan-conflict-warning"><AlertTriangle /><span><strong>Local milestones, branches, and dependencies will be replaced.</strong> Repository clone paths and GitHub credentials stay local and are preserved.</span></div>
      <div className="plan-source-list">
        {selectable.map((source) => (
          <button className={source.repository_id === selected?.repository_id ? "selected" : ""} key={source.repository_id} onClick={() => onSelect(source.repository_id)} type="button">
            <span className="plan-source-radio">{source.repository_id === selected?.repository_id && <Check />}</span>
            <span><strong>{source.github_full_name}</strong><small>{source.path}</small></span>
            <span><strong>Revision {source.revision}</strong><code>{source.fingerprint?.slice(0, 10)}</code></span>
          </button>
        ))}
      </div>
      <div className="modal-actions">
        <button className="button button-ghost" disabled={syncing} onClick={onClose} type="button">Cancel</button>
        <button className="button button-danger" disabled={syncing || !selected} onClick={() => selected && onImport(selected.repository_id)} type="button">
          {syncing ? "Replacing local plan…" : `Use revision ${selected?.revision ?? ""} from Git`}
        </button>
      </div>
    </Modal>
  );
}

function PlanStepCard({
  dashboard,
  onClick,
  step,
}: {
  dashboard: Dashboard;
  onClick: () => void;
  step: Step;
}) {
  const dependencyNames = step.depends_on.map((stepId) => {
    const dependency = dashboard.steps.find((candidate) => candidate.step_id === stepId);
    return dependency ? stripTitle(dependency.title) : stepId;
  });

  return (
    <button
      aria-label={`Edit ${step.step_id}: ${stripTitle(step.title)}`}
      className={`plan-step-card plan-step-${step.computed_state}`}
      onClick={onClick}
      type="button"
    >
      <header>
        <span><code>{step.step_id}</code><small>{step.repository_alias}</small></span>
        <StatusBadge status={step.computed_state} />
      </header>
      <strong>{stripTitle(step.title)}</strong>
      <p>{step.required_result}</p>
      <div className="plan-step-prerequisites">
        <Route />
        <span>
          <small>{dependencyNames.length ? "Needs first" : "Starts immediately"}</small>
          <strong>{dependencyNames.length ? dependencyNames.join(" · ") : "No prerequisites"}</strong>
        </span>
      </div>
      <footer><Pencil />Edit step</footer>
    </button>
  );
}

function StepEditorModal({
  dashboard,
  onClose,
  onOpenDelivery,
  onSaved,
  step,
}: {
  dashboard: Dashboard;
  onClose: () => void;
  onOpenDelivery: (step: Step) => void;
  onSaved: () => Promise<void>;
  step: Step | null;
}) {
  const initialRepositoryId = step?.repository_id || dashboard.repositories[0]?.repository_id || "";
  const [repositoryId, setRepositoryId] = useState(initialRepositoryId);
  const [name, setName] = useState(stripTitle(step?.title || ""));
  const [requiredResult, setRequiredResult] = useState(step?.required_result || "");
  const [branch, setBranch] = useState(step?.branch_name || "");
  const [branchEdited, setBranchEdited] = useState(Boolean(step));
  const [dependencies, setDependencies] = useState<string[]>(step?.depends_on || []);
  const [dependencyQuery, setDependencyQuery] = useState("");
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const stepId = step?.step_id || suggestStepId(dashboard, repositoryId);
  const suggestedBranch = `${dashboard.project.release.toLowerCase()}/${slugify(name || "new-step")}`;
  const branchName = branchEdited ? branch : suggestedBranch;
  const blockedDependencies = step
    ? downstreamStepIds(dashboard.steps, step.step_id)
    : new Set<string>();
  const depthById = stepDepths(dashboard.steps);
  const dependencyLevel = dependencies.length
    ? Math.max(...dependencies.map((id) => depthById.get(id) || 0)) + 1
    : 0;
  const dependencyCandidates = dashboard.steps.filter((candidate) => {
    if (candidate.step_id === step?.step_id) return false;
    if (blockedDependencies.has(candidate.step_id)) return false;
    const needle = dependencyQuery.trim().toLowerCase();
    return !needle || `${candidate.step_id} ${candidate.title} ${candidate.repository_alias}`.toLowerCase().includes(needle);
  }).sort((left, right) => {
    const selectedDifference = Number(dependencies.includes(right.step_id)) - Number(dependencies.includes(left.step_id));
    if (selectedDifference) return selectedDifference;
    const stateRank = { completed: 0, ready: 1, pending: 2, in_review: 3, planned: 4, blocked: 5, cancelled: 6 };
    return stateRank[left.computed_state] - stateRank[right.computed_state]
      || (depthById.get(left.step_id) || 0) - (depthById.get(right.step_id) || 0)
      || left.step_id.localeCompare(right.step_id);
  });
  const directDependents = step
    ? dashboard.steps.filter((candidate) => candidate.depends_on.includes(step.step_id))
    : [];
  const deleteBlockedReason = !step
    ? null
    : step.artifacts.length
      ? "This step already has GitHub delivery evidence. Keep it in the plan as part of the project history."
      : directDependents.length
        ? `Remove it from ${directDependents.map((candidate) => candidate.step_id).join(", ")} first.`
        : null;

  function chooseRepository(repository: Repository) {
    setRepositoryId(repository.repository_id);
  }

  function toggleDependency(candidate: Step) {
    if (blockedDependencies.has(candidate.step_id)) return;
    setDependencies((current) =>
      current.includes(candidate.step_id)
        ? current.filter((value) => value !== candidate.step_id)
        : [...current, candidate.step_id],
    );
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const body = {
      repository_id: repositoryId,
      title: `[TASK][${dashboard.project.release.toUpperCase()}] ${name.trim()}`,
      required_result: requiredResult.trim(),
      branch_name: branchName.trim(),
      depends_on: dependencies,
    };
    try {
      if (step) {
        await api(`/api/projects/${dashboard.project.project_id}/steps/${step.step_id}`, {
          method: "PUT",
          body: JSON.stringify(body),
        });
      } else {
        await api(`/api/projects/${dashboard.project.project_id}/steps`, {
          method: "POST",
          body: JSON.stringify({
            ...body,
            step_id: stepId,
            release: dashboard.project.release,
          }),
        });
      }
      await onSaved();
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The plan step could not be saved");
    } finally {
      setSaving(false);
    }
  }

  async function deleteStep() {
    if (!step || deleteBlockedReason) return;
    setDeleting(true);
    setError(null);
    try {
      await api<void>(
        `/api/projects/${dashboard.project.project_id}/steps/${step.step_id}`,
        { method: "DELETE" },
      );
      await onSaved();
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The plan step could not be deleted");
    } finally {
      setDeleting(false);
    }
  }

  const valid = Boolean(repositoryId && name.trim() && requiredResult.trim() && branchName.trim());

  return (
    <Modal
      description={step ? "Change the result, owner, branch, or visible prerequisite cards." : "Describe one result and connect it to the work that must happen first."}
      extraWide
      onClose={onClose}
      title={step ? `Edit ${step.step_id}` : "Create a plan step"}
    >
      <form className="visual-step-editor" onSubmit={save}>
        {error && <ErrorBanner message={error} />}
        <section className="visual-step-details">
          <header><span>1</span><div><strong>What are we building?</strong><small>Use plain language. DPT adds the title convention and step ID.</small></div></header>
          <div className="visual-step-identity">
            <span><small>Automatic ID</small><code>{stepId}</code></span>
            <span><small>Milestone stage</small><strong>Stage {dependencyLevel + 1}</strong></span>
          </div>
          <label>
            <span>Short step name</span>
            <input autoFocus name="title" onChange={(event) => setName(event.target.value)} placeholder="Define Provider Manifest Exchange Contract" required value={name} />
            <small>The final title will be `[TASK][{dashboard.project.release.toUpperCase()}] {name || "…"}`.</small>
          </label>
          <label>
            <span>What must be true when this is finished?</span>
            <textarea name="required_result" onChange={(event) => setRequiredResult(event.target.value)} placeholder="A Provider can publish a validated, versioned manifest that the Agent can retrieve after handshake." required rows={5} value={requiredResult} />
          </label>

          <div className="visual-step-section-heading"><span>2</span><div><strong>Where does it belong?</strong><small>Choose the repository visually.</small></div></div>
          <div className="repository-choice-grid">
            {dashboard.repositories.map((repository) => (
              <button
                className={repositoryId === repository.repository_id ? "selected" : ""}
                key={repository.repository_id}
                onClick={() => chooseRepository(repository)}
                type="button"
              >
                <span>{repository.alias.slice(0, 2).toUpperCase()}</span>
                <div><strong>{repository.alias}</strong><small>{repository.github_full_name}</small></div>
                {repositoryId === repository.repository_id && <Check />}
              </button>
            ))}
          </div>
          <label>
            <span>Development branch</span>
            <div className="branch-name-input"><GitBranch /><input name="branch_name" onChange={(event) => { setBranchEdited(true); setBranch(event.target.value); }} required value={branchName} /></div>
            <small>DPT suggested this from the step name. Change it only when the branch already has another name.</small>
          </label>
        </section>

        <section className="visual-dependency-editor">
          <header><span>3</span><div><strong>What must happen first?</strong><small>Click cards—no IDs to remember or type.</small></div></header>
          <div className="dependency-path-preview">
            <div>
              {dependencies.length ? dependencies.map((dependencyId) => {
                const dependency = dashboard.steps.find((candidate) => candidate.step_id === dependencyId);
                return dependency ? <span key={dependencyId}><code>{dependencyId}</code>{stripTitle(dependency.title)}</span> : null;
              }) : <span className="foundation-choice"><Sparkles />Nothing—this starts immediately</span>}
            </div>
            <ArrowRight />
            <strong><code>{stepId}</code>{name || "This new step"}</strong>
          </div>
          <SearchInput value={dependencyQuery} onChange={setDependencyQuery} placeholder="Find prerequisite work" />
          <div className="dependency-choice-list">
            {dependencyCandidates.map((candidate) => {
              const selected = dependencies.includes(candidate.step_id);
              const disabled = blockedDependencies.has(candidate.step_id);
              return (
                <button
                  aria-pressed={selected}
                  className={`${selected ? "selected" : ""}${disabled ? " disabled" : ""}`}
                  disabled={disabled}
                  key={candidate.step_id}
                  onClick={() => toggleDependency(candidate)}
                  type="button"
                >
                  <span className="dependency-choice-check">{selected ? <Check /> : <CircleDot />}</span>
                  <div>
                    <span><code>{candidate.step_id}</code><small>{candidate.repository_alias}</small></span>
                    <strong>{stripTitle(candidate.title)}</strong>
                    <small>{`Currently ${candidate.computed_state.replace("_", " ")}`}</small>
                  </div>
                  <ChevronRight />
                </button>
              );
            })}
            {!dependencyCandidates.length && <p className="dependency-choice-empty">No steps match this search.</p>}
          </div>
        </section>

        <footer className="visual-step-editor-actions">
          <div>
            {step && (
              <button className="button button-ghost plan-delete-step" onClick={() => setConfirmDelete(true)} type="button"><Trash2 />Delete step</button>
            )}
            {step && (
              <button className="button button-secondary" onClick={() => { onClose(); onOpenDelivery(step); }} type="button">Open delivery details</button>
            )}
          </div>
          <div>
            <button className="button button-ghost" disabled={saving || deleting} onClick={onClose} type="button">Cancel</button>
            <button className="button button-primary" disabled={!valid || saving || deleting}><Check />{saving ? "Saving…" : step ? "Save changes" : "Create step"}</button>
          </div>
        </footer>
      </form>

      {confirmDelete && step && (
        <div className="plan-delete-overlay">
          <section>
            <span><Trash2 /></span>
            <h3>Delete {step.step_id}?</h3>
            <p>This removes the step from the canonical plan. It never deletes code, branches, Issues, or pull requests.</p>
            {deleteBlockedReason && <ErrorBanner message={deleteBlockedReason} />}
            <div className="modal-actions">
              <button className="button button-ghost" disabled={deleting} onClick={() => setConfirmDelete(false)} type="button">Keep step</button>
              <button className="button button-danger" disabled={Boolean(deleteBlockedReason) || deleting} onClick={deleteStep} type="button"><Trash2 />{deleting ? "Deleting…" : "Delete from plan"}</button>
            </div>
          </section>
        </div>
      )}
    </Modal>
  );
}

function stripTitle(title: string) {
  return title.replace(/^\[[^\]]+\]\[[^\]]+\]\s*/, "");
}

function slugify(value: string) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "new-step";
}

function suggestStepId(dashboard: Dashboard, repositoryId: string) {
  const repository = dashboard.repositories.find(
    (candidate) => candidate.repository_id === repositoryId,
  );
  const repositorySteps = dashboard.steps.filter(
    (candidate) => candidate.repository_id === repositoryId,
  );
  const parsed = repositorySteps.flatMap((candidate) => {
    const match = candidate.step_id.match(/^([A-Za-z]+)(\d+)$/);
    return match ? [{ prefix: match[1].toUpperCase(), number: Number(match[2]) }] : [];
  });
  const prefix = parsed[0]?.prefix || repository?.alias.slice(0, 1).toUpperCase() || "S";
  const nextNumber = Math.max(-1, ...parsed.filter((item) => item.prefix === prefix).map((item) => item.number)) + 1;
  return `${prefix}${nextNumber}`;
}

function downstreamStepIds(steps: Step[], stepId: string) {
  const downstream = new Set<string>();
  let changed = true;
  while (changed) {
    changed = false;
    for (const candidate of steps) {
      if (
        !downstream.has(candidate.step_id) &&
        candidate.depends_on.some((dependency) => dependency === stepId || downstream.has(dependency))
      ) {
        downstream.add(candidate.step_id);
        changed = true;
      }
    }
  }
  return downstream;
}

function stepDepths(steps: Step[]) {
  const depthById = new Map<string, number>();
  const byId = new Map(steps.map((step) => [step.step_id, step]));
  function depth(step: Step, visiting = new Set<string>()): number {
    const known = depthById.get(step.step_id);
    if (known !== undefined) return known;
    if (visiting.has(step.step_id)) return 0;
    const nextVisiting = new Set(visiting).add(step.step_id);
    const dependencies = step.depends_on.flatMap((id) => {
      const dependency = byId.get(id);
      return dependency ? [dependency] : [];
    });
    const result = dependencies.length
      ? Math.max(...dependencies.map((dependency) => depth(dependency, nextVisiting))) + 1
      : 0;
    depthById.set(step.step_id, result);
    return result;
  }
  for (const step of steps) depth(step);
  return depthById;
}

function dependencyLayers(steps: Step[]) {
  const depthById = stepDepths(steps);
  const layers: Step[][] = [];
  for (const step of steps) {
    const level = depthById.get(step.step_id) || 0;
    (layers[level] ||= []).push(step);
  }
  return layers;
}
