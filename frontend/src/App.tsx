import {
  Activity as ActivityIcon,
  ArrowLeft,
  ArrowRight,
  Blocks,
  Bug,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CircleDot,
  CloudCog,
  ExternalLink,
  FileCode2,
  FileDiff,
  FolderGit2,
  FolderInput,
  GitBranch,
  GitCommitHorizontal,
  Github,
  GitMerge,
  GitPullRequest,
  Layers3,
  LayoutDashboard,
  Link2,
  ListChecks,
  LockKeyhole,
  LogOut,
  MessageSquare,
  Network,
  PanelLeft,
  PanelLeftClose,
  PanelLeftOpen,
  Play,
  Plus,
  RefreshCw,
  Route,
  Settings,
  ShieldCheck,
  Sparkles,
  Tag,
  Trash2,
  Unlink,
  UserPlus,
  Users,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { api, apiStream } from "./api";
import { Markdown } from "./Markdown";
import { PlanEditorPage } from "./PlanEditor";
import { ReleaseProgressMap } from "./ReleaseProgressMap";
import type {
  Activity,
  Artifact,
  Blocker,
  Dashboard,
  DeviceAuthorization,
  DeviceAuthorizationPoll,
  GitHubAccount,
  GitHubRepository,
  GitHubTimelineItem,
  Project,
  ProjectImportInspection,
  Repository,
  Session,
  Step,
  StepState,
} from "./types";
import {
  EmptyState,
  ErrorBanner,
  Loading,
  Modal,
  PageHeader,
  SearchInput,
  StateForgeLogo,
  StatusBadge,
} from "./ui";

type PageName =
  | "overview"
  | "roadmap"
  | "work"
  | "repositories"
  | "plan"
  | "reconciliation"
  | "activity"
  | "settings"
  | "connect-github";

const pageItems: Array<{ page: PageName; label: string; icon: ReactNode }> = [
  { page: "overview", label: "Overview", icon: <LayoutDashboard /> },
  { page: "roadmap", label: "Roadmap", icon: <Network /> },
  { page: "work", label: "Work queue", icon: <ListChecks /> },
  { page: "repositories", label: "Repositories", icon: <FolderGit2 /> },
  { page: "plan", label: "Plan editor", icon: <FileCode2 /> },
  { page: "reconciliation", label: "Reconciliation", icon: <FileDiff /> },
  { page: "activity", label: "Activity", icon: <ActivityIcon /> },
  { page: "settings", label: "Settings", icon: <Settings /> },
];

type ReconciliationPhase =
  | "starting"
  | "plan_fetching"
  | "plan_applying"
  | "fetching"
  | "caching"
  | "dependencies"
  | "validating"
  | "updating";

type ReconciliationProgress = {
  phase: ReconciliationPhase;
  title: string;
  detail: string;
  repository: string | null;
  repository_index: number;
  repository_count: number;
  artifacts: number;
};

type ReconciliationStreamEvent =
  | ({ type: "progress" } & ReconciliationProgress)
  | {
      type: "complete";
      repositories: number;
      artifacts: number;
      plan_status: "imported" | "current" | "conflict" | "unavailable" | "local_changes" | "local_ahead";
      plan_message: string;
      plan_revision: number;
      plan_source_branch: string;
      plan_errors: Array<{ repository: string; message: string }>;
    }
  | { type: "error"; message: string };

let projectTransitionTimeout: number | undefined;

function navigate(path: string) {
  const currentProjectId = window.location.hash.match(
    /^#\/projects\/([^/]+)/,
  )?.[1];
  const targetProjectId = path.match(/^#?\/?projects\/([^/]+)/)?.[1];
  const reduceMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)",
  ).matches;

  if (Boolean(currentProjectId) !== Boolean(targetProjectId) && !reduceMotion) {
    window.clearTimeout(projectTransitionTimeout);
    document.documentElement.dataset.projectTransition = targetProjectId
      ? "enter"
      : "exit";
    projectTransitionTimeout = window.setTimeout(() => {
      delete document.documentElement.dataset.projectTransition;
    }, 220);
  }

  window.location.hash = path;
}

function useRoute() {
  const [hash, setHash] = useState(window.location.hash || "#/projects");
  useEffect(() => {
    const update = () => setHash(window.location.hash || "#/projects");
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  return {
    raw: hash,
    root: parts[0] || "projects",
    projectId: parts[0] === "projects" ? parts[1] : undefined,
    page: (parts[2] || "overview") as PageName,
  };
}

function timeAgo(value: string) {
  const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function githubProfileUrl(session: Session | null) {
  if (!session?.authenticated || !session.user?.login) return null;
  return `https://github.com/${encodeURIComponent(session.user.login)}`;
}

function githubBranchUrl(repository: string, branch: string) {
  const encodedBranch = branch
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
  return `https://github.com/${repository}/tree/${encodedBranch}`;
}

function App() {
  const route = useRoute();
  const [session, setSession] = useState<Session | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reconciling, setReconciling] = useState(false);
  const [reconciliationProgress, setReconciliationProgress] =
    useState<ReconciliationProgress | null>(null);
  const [createProjectOpen, setCreateProjectOpen] = useState(false);
  const [importProjectOpen, setImportProjectOpen] = useState(false);
  const [repositoryOpen, setRepositoryOpen] = useState(false);
  const [selectedStep, setSelectedStep] = useState<Step | null>(null);
  const [selectedBlocker, setSelectedBlocker] = useState<Blocker | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => window.localStorage.getItem("stateforge-dpt:sidebar-collapsed") === "true",
  );

  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(() => setNotice(null), 3000);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  useEffect(() => {
    if (!error || !session) return;
    const timeout = window.setTimeout(() => setError(null), 3000);
    return () => window.clearTimeout(timeout);
  }, [error, session]);

  const loadProjects = useCallback(async () => {
    const values = await api<Project[]>("/api/projects");
    setProjects(values);
  }, []);

  const loadDashboard = useCallback(async (projectId: string) => {
    const value = await api<Dashboard>(`/api/projects/${projectId}`);
    setDashboard(value);
    setSelectedStep((current) =>
      current
        ? value.steps.find((step) => step.step_id === current.step_id) || null
        : null,
    );
    setSelectedBlocker((current) =>
      current
        ? value.blockers.find(
            (blocker) => blocker.blocker_id === current.blocker_id,
          ) || null
        : null,
    );
  }, []);

  const finishGitHubConnection = useCallback(async () => {
    const sessionValue = await api<Session>("/api/session");
    setSession(sessionValue);
    navigate(
      route.projectId
        ? `/projects/${route.projectId}/overview`
        : "/projects",
    );
  }, [route.projectId]);

  useEffect(() => {
    Promise.all([
      api<Session>("/api/session"),
      api<Project[]>("/api/projects"),
    ])
      .then(([sessionValue, projectValues]) => {
        setSession(sessionValue);
        setProjects(projectValues);
      })
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "DPT is unavailable"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!route.projectId) {
      setDashboard(null);
      return;
    }
    setLoading(true);
    loadDashboard(route.projectId)
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "Project could not be loaded"))
      .finally(() => setLoading(false));
  }, [loadDashboard, route.projectId]);

  useEffect(() => {
    if (route.page !== "activity" || !route.projectId) return;
    api<Activity[]>(`/api/projects/${route.projectId}/activity`)
      .then(setActivity)
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "Activity could not be loaded"));
  }, [route.page, route.projectId]);

  function showError(caught: unknown) {
    setError(caught instanceof Error ? caught.message : "The operation failed");
  }

  async function reconcile() {
    if (!route.projectId || reconciling) return;
    if (!session?.authenticated) {
      navigate(`/projects/${route.projectId}/connect-github`);
      return;
    }
    setReconciling(true);
    setReconciliationProgress({
      phase: "starting",
      title: "Preparing reconciliation",
      detail:
        "Preparing the parent-branch plan check and authenticated GitHub requests.",
      repository: null,
      repository_index: 0,
      repository_count: dashboard?.repositories.length || 0,
      artifacts: 0,
    });
    setNotice(null);
    const minimumProgressDisplay = new Promise<void>((resolve) => {
      window.setTimeout(resolve, 750);
    });
    try {
      const result = {
        repositories: 0,
        artifacts: 0,
        complete: false,
        planStatus: "current",
        planMessage: "Plan is current",
        planRevision: dashboard?.project.revision || 0,
        planSourceBranch: dashboard?.project.plan_source_branch || "",
        planErrors: [] as Array<{ repository: string; message: string }>,
      };
      await apiStream<ReconciliationStreamEvent>(
        `/api/projects/${route.projectId}/reconcile/stream`,
        (event) => {
          if (event.type === "progress") {
            setReconciliationProgress({
              phase: event.phase,
              title: event.title,
              detail: event.detail,
              repository: event.repository,
              repository_index: event.repository_index,
              repository_count: event.repository_count,
              artifacts: event.artifacts,
            });
          } else if (event.type === "complete") {
            result.repositories = event.repositories;
            result.artifacts = event.artifacts;
            result.planStatus = event.plan_status;
            result.planMessage = event.plan_message;
            result.planRevision = event.plan_revision;
            result.planSourceBranch = event.plan_source_branch;
            result.planErrors = event.plan_errors;
            result.complete = true;
          } else {
            throw new Error(event.message);
          }
        },
        { method: "POST" },
      );
      if (!result.complete) {
        throw new Error("Reconciliation ended without a result");
      }
      setReconciliationProgress({
        phase: "updating",
        title: "Updating this page",
        detail:
          "Loading the rebuilt milestone states and reconciliation findings from DPT.",
        repository: null,
        repository_index: result.repositories,
        repository_count: result.repositories,
        artifacts: result.artifacts,
      });
      await loadDashboard(route.projectId);
      await minimumProgressDisplay;
      const planNotice = result.planStatus === "imported"
        ? `Imported plan revision ${result.planRevision} from ${result.planSourceBranch}. `
        : result.planStatus === "conflict"
          ? "Plan conflict needs review in the Plan editor. "
          : "";
      const sourceWarning = result.planErrors.length
        ? ` ${result.planErrors.length} plan source ${result.planErrors.length === 1 ? "warning" : "warnings"}.`
        : "";
      setNotice(`${planNotice}Reconciled ${result.artifacts} artifacts across ${result.repositories} repositories.${sourceWarning}`);
    } catch (caught) {
      await minimumProgressDisplay;
      setNotice(null);
      showError(caught);
    } finally {
      setReconciling(false);
      setReconciliationProgress(null);
    }
  }

  async function generate() {
    if (!route.projectId) return false;
    try {
      const result = await api<{ revision: number; repositories: number }>(
        `/api/projects/${route.projectId}/plan/generate`,
        { method: "POST", body: JSON.stringify({ force: false }) },
      );
      await loadDashboard(route.projectId);
      setNotice(`Generated revision ${result.revision} into ${result.repositories} repositories.`);
      return true;
    } catch (caught) {
      showError(caught);
      return false;
    }
  }

  async function publishPlanThroughGit() {
    if (!route.projectId) return false;
    try {
      const result = await api<{
        revision: number;
        repositories: number;
        update_branch: string;
        target_branch: string;
      }>(`/api/projects/${route.projectId}/plan/publish-git`, { method: "POST" });
      await loadDashboard(route.projectId);
      setNotice(
        `Published revision ${result.revision} through ${result.update_branch} `
        + `and merged it into ${result.target_branch} across ${result.repositories} repositories.`,
      );
      return true;
    } catch (caught) {
      showError(caught);
      return false;
    }
  }

  if (loading && !session) return <LoadingScreen />;
  if (error && !session) return <FatalError message={error} />;
  if (route.root === "login") {
    return <LoginPage session={session} />;
  }
  if (route.root === "connect-github") {
    return (
      <StandaloneGitHubConnectPage
        session={session}
        onConnected={finishGitHubConnection}
      />
    );
  }
  if (!route.projectId) {
    return (
      <ProjectsPage
        projects={projects}
        session={session}
        onCreate={() => setCreateProjectOpen(true)}
        onImport={() => setImportProjectOpen(true)}
        onRemoved={loadProjects}
        modal={
          <>
            {createProjectOpen && (
              <CreateProjectModal
                onClose={() => setCreateProjectOpen(false)}
                onCreated={async (project) => {
                  setCreateProjectOpen(false);
                  await loadProjects();
                  navigate(`/projects/${project.project_id}/overview`);
                }}
              />
            )}
            {importProjectOpen && (
              <ImportProjectModal
                onClose={() => setImportProjectOpen(false)}
                onImported={async (project) => {
                  setImportProjectOpen(false);
                  await loadProjects();
                  navigate(`/projects/${project.project_id}/overview`);
                }}
              />
            )}
          </>
        }
      />
    );
  }

  const project = dashboard?.project;
  return (
    <div className={sidebarCollapsed ? "app-shell sidebar-collapsed" : "app-shell"}>
      <Sidebar
        collapsed={sidebarCollapsed}
        page={route.page}
        project={project}
        projects={projects}
        session={session}
        onAddRepository={() => setRepositoryOpen(true)}
        onConnectGitHub={() =>
          navigate(`/projects/${route.projectId}/connect-github`)
        }
        onToggleCollapsed={() => {
          const collapsed = !sidebarCollapsed;
          setSidebarCollapsed(collapsed);
          window.localStorage.setItem("stateforge-dpt:sidebar-collapsed", String(collapsed));
        }}
      />
      <main className="main-shell">
        <Topbar
          project={project}
          session={session}
          onConnectGitHub={() =>
            navigate(`/projects/${route.projectId}/connect-github`)
          }
          onReconcile={reconcile}
          reconciling={reconciling}
        />
        {error && <div className="floating-message"><ErrorBanner message={error} /><button onClick={() => setError(null)}><X /></button></div>}
        {notice && <div className="notice-message"><CheckCircle2 />{notice}<button onClick={() => setNotice(null)}><X /></button></div>}
        <div className="page-content">
          {loading || !dashboard ? (
            <Loading label="Loading project" />
          ) : (
            <CurrentPage
              activity={activity}
              dashboard={dashboard}
              onAddRepository={() => setRepositoryOpen(true)}
              onGenerate={generate}
              onPublishGit={publishPlanThroughGit}
              onGitHubConnected={finishGitHubConnection}
              onOpenStep={(step) => setSelectedStep(step)}
              onOpenBlocker={(blocker) => {
                setSelectedStep(null);
                setSelectedBlocker(blocker);
              }}
              onReconcile={reconcile}
              onRemoveRepository={async () => {
                await Promise.all([
                  loadDashboard(dashboard.project.project_id),
                  loadProjects(),
                ]);
              }}
              onPlanChanged={() => loadDashboard(dashboard.project.project_id)}
              page={route.page}
              reconciliationProgress={reconciliationProgress}
              reconciling={reconciling}
              session={session}
            />
          )}
        </div>
      </main>
      {repositoryOpen && project && (
        <AddRepositoryModal
          projectId={project.project_id}
          session={session}
          onClose={() => setRepositoryOpen(false)}
          onCreated={async () => {
            setRepositoryOpen(false);
            await loadDashboard(project.project_id);
          }}
        />
      )}
      {selectedStep && dashboard && (
        <StepDetail
          dashboard={dashboard}
          session={session}
          step={selectedStep}
          onClose={() => setSelectedStep(null)}
          onChanged={() => loadDashboard(dashboard.project.project_id)}
          onOpenBlocker={(blocker) => {
            setSelectedStep(null);
            setSelectedBlocker(blocker);
          }}
          onSelect={setSelectedStep}
        />
      )}
      {selectedBlocker && dashboard && (
        <BlockerDetail
          blocker={selectedBlocker}
          dashboard={dashboard}
          session={session}
          onChanged={() => loadDashboard(dashboard.project.project_id)}
          onClose={() => setSelectedBlocker(null)}
          onOpenStep={(step) => {
            setSelectedBlocker(null);
            setSelectedStep(step);
          }}
        />
      )}
    </div>
  );
}

function LoadingScreen() {
  return <div className="full-screen-state"><StateForgeLogo /><Loading label="Starting DPT" /></div>;
}

function FatalError({ message }: { message: string }) {
  return (
    <div className="full-screen-state">
      <StateForgeLogo />
      <ErrorBanner message={message} />
      <p>Start the local DPT backend, then reload this page.</p>
    </div>
  );
}

function LoginPage({
  session,
}: {
  session: Session | null;
}) {
  const [authorization, setAuthorization] =
    useState<DeviceAuthorization | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    if (!authorization) return;
    let cancelled = false;
    let timer = 0;

    async function poll() {
      try {
        const result = await api<DeviceAuthorizationPoll>(
          `/api/auth/github/device/${authorization?.authorization_id}/poll`,
          { method: "POST" },
        );
        if (cancelled) return;
        if (result.status === "complete") {
          window.location.assign("/#/projects");
          return;
        }
        timer = window.setTimeout(
          poll,
          (result.retry_after || authorization?.interval || 5) * 1000,
        );
      } catch (caught) {
        if (cancelled) return;
        setAuthorization(null);
        setError(
          caught instanceof Error
            ? caught.message
            : "GitHub authorization failed",
        );
      }
    }

    timer = window.setTimeout(poll, authorization.interval * 1000);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [authorization]);

  async function startAuthentication() {
    setError(null);
    setStarting(true);
    try {
      const value = await api<DeviceAuthorization>(
        "/api/auth/github/device",
        { method: "POST" },
      );
      setAuthorization(value);
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "GitHub login could not start",
      );
    } finally {
      setStarting(false);
    }
  }

  return (
    <>
      <main className="login-page">
        <div className="login-grid" aria-hidden="true" />
        <section className="login-card">
          <StateForgeLogo />
          <div className="login-copy">
            <span className="eyebrow">Local development control</span>
            <h1>Keep every repository moving as one project.</h1>
            <p>DPT connects implementation plans, local clones, and trusted GitHub evidence without losing dependency control.</p>
          </div>
          {error && <ErrorBanner message={error} />}
          {session?.authenticated ? (
            <button className="button button-primary button-wide" onClick={() => navigate("/projects")}>
              Continue as {session.user?.login}<ArrowRight />
            </button>
          ) : (
            <button
              className="button button-github button-wide"
              disabled={starting}
              onClick={startAuthentication}
            >
              <Github />{starting ? "Connecting to GitHub…" : "Continue with GitHub"}
            </button>
          )}
          <button className="button button-ghost button-wide" onClick={() => navigate("/projects")}>Continue with local repositories</button>
          <footer><LockKeyhole />Credentials stay encrypted on this machine.</footer>
        </section>
        <aside className="login-proof">
          <div className="proof-node proof-node-plan"><FileCode2 /><span><strong>Declared plan</strong>Exact cross-repository work</span></div>
          <div className="proof-line"><span /></div>
          <div className="proof-node proof-node-dpt"><img src="/assets/stateforge/svg/stateforge-mark-white.svg" alt="" /><span><strong>StateForge DPT</strong>Reconciliation and control</span></div>
          <div className="proof-line"><span /></div>
          <div className="proof-node proof-node-github"><Github /><span><strong>GitHub evidence</strong>Issues, reviews, and merges</span></div>
        </aside>
      </main>
      {authorization && (
        <Modal
          title="Authorize StateForge DPT"
          description="Confirm this local DPT installation with your GitHub account."
          onClose={() => setAuthorization(null)}
        >
          <div className="device-authorization">
            <span className="device-authorization-label">Your one-time code</span>
            <button
              className="device-code"
              onClick={() => navigator.clipboard.writeText(authorization.user_code)}
              type="button"
            >
              {authorization.user_code}
              <small>Click to copy</small>
            </button>
            <p>Open GitHub, enter this code, and approve the requested repository access.</p>
            <a
              className="button button-github button-wide"
              href={authorization.verification_uri}
              rel="noreferrer"
              target="_blank"
            >
              <Github />Open GitHub authorization<ExternalLink />
            </a>
            <div className="device-waiting">
              <RefreshCw />Waiting for GitHub confirmation…
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}

function ProjectsPage({
  projects,
  session,
  onCreate,
  onImport,
  onRemoved,
  modal,
}: {
  projects: Project[];
  session: Session | null;
  onCreate: () => void;
  onImport: () => void;
  onRemoved: () => Promise<void>;
  modal: ReactNode;
}) {
  const profileUrl = githubProfileUrl(session);
  const [projectToRemove, setProjectToRemove] = useState<Project | null>(null);
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);

  async function confirmRemoval() {
    if (!projectToRemove) return;
    setRemoving(true);
    setRemoveError(null);
    try {
      await api<void>(`/api/projects/${projectToRemove.project_id}`, {
        method: "DELETE",
      });
      await onRemoved();
      setProjectToRemove(null);
    } catch (caught) {
      setRemoveError(
        caught instanceof Error ? caught.message : "Project could not be removed",
      );
    } finally {
      setRemoving(false);
    }
  }

  return (
    <main className="projects-page">
      <header className="projects-topbar">
        <StateForgeLogo />
        {profileUrl ? (
          <a
            className="account-chip"
            href={profileUrl}
            rel="noreferrer"
            target="_blank"
            title="Open GitHub profile"
          >
            {session?.user?.avatar_url ? <img src={session.user.avatar_url} alt="" /> : <Github />}
            <span>{session?.user?.login}</span>
            <ExternalLink className="account-chip-external" />
          </a>
        ) : (
          <button
            className="account-chip"
            onClick={() => navigate("/connect-github")}
            type="button"
          >
            <Github />
            <span>Local mode</span>
          </button>
        )}
      </header>
      <section className="projects-content">
        <PageHeader
          eyebrow="Workspaces"
          title="Development projects"
          description="Open a local project or import its complete plan from any Git clone carrying a published DPT file."
          actions={<><button className="button button-secondary" onClick={onImport}><FolderInput />Import from repository</button><button className="button button-primary" onClick={onCreate}><Plus />New project</button></>}
        />
        {projects.length ? (
          <div className="project-grid">
            {projects.map((project) => (
              <article className="project-card" key={project.project_id}>
                <button className="project-card-open" onClick={() => navigate(`/projects/${project.project_id}/overview`)}>
                  <span className="project-icon"><Layers3 /></span>
                  <div className="project-card-head"><span>{project.release}</span><small>Revision {project.revision}</small></div>
                  <h2>{project.name}</h2>
                  <p>{project.repository_count || 0} repositories · {project.step_count || 0} implementation steps</p>
                  <footer><span>Updated {timeAgo(project.updated_at)}</span><ArrowRight /></footer>
                </button>
                <button
                  aria-label={`Remove ${project.name} from DPT`}
                  className="project-card-remove"
                  onClick={() => {
                    setRemoveError(null);
                    setProjectToRemove(project);
                  }}
                  title="Remove project from DPT"
                  type="button"
                >
                  <Trash2 />
                </button>
              </article>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<Layers3 />}
            title="Create or import a DPT project"
            description="Starting fresh creates an empty plan. Importing a local Git clone recreates the shared repositories, milestones, branches, and dependencies."
            action={<div className="empty-state-actions"><button className="button button-secondary" onClick={onImport}><FolderInput />Import existing project</button><button className="button button-primary" onClick={onCreate}><Plus />Create new project</button></div>}
          />
        )}
      </section>
      {modal}
      {projectToRemove && (
        <Modal
          title="Remove project from DPT?"
          description="This removes only this local DPT working copy."
          onClose={() => !removing && setProjectToRemove(null)}
        >
          <div className="repository-removal">
            <span className="repository-removal-icon"><Trash2 /></span>
            <div>
              <strong>{projectToRemove.name}</strong>
              <p>
                DPT will forget this project, its repository associations, plan,
                and cached GitHub evidence. Local clones, branches, files, and the
                GitHub repositories will not be changed.
              </p>
            </div>
          </div>
          <div className="project-removal-summary">
            <span><strong>{projectToRemove.repository_count || 0}</strong> repositories</span>
            <span><strong>{projectToRemove.step_count || 0}</strong> milestones</span>
            <span><strong>{projectToRemove.revision}</strong> revision</span>
          </div>
          {removeError && <ErrorBanner message={removeError} />}
          <div className="modal-actions">
            <button className="button button-ghost" disabled={removing} onClick={() => setProjectToRemove(null)} type="button">Cancel</button>
            <button className="button button-danger" disabled={removing} onClick={confirmRemoval} type="button">
              <Trash2 />{removing ? "Removing…" : "Remove from DPT"}
            </button>
          </div>
        </Modal>
      )}
    </main>
  );
}

function StandaloneGitHubConnectPage({
  session,
  onConnected,
}: {
  session: Session | null;
  onConnected: () => Promise<void>;
}) {
  return (
    <main className="projects-page">
      <header className="projects-topbar">
        <StateForgeLogo />
        <button
          className="button button-secondary"
          onClick={() => navigate("/projects")}
          type="button"
        >
          <ArrowLeft />Back to projects
        </button>
      </header>
      <section className="projects-content github-connect-content">
        <PageHeader
          eyebrow="GitHub connection"
          title="Connect StateForge DPT to GitHub"
          description="Authorize this local DPT installation to read delivery evidence and manage the GitHub workflow you initiate."
        />
        <GitHubConnectPanel
          session={session}
          onConnected={onConnected}
        />
      </section>
    </main>
  );
}

function GitHubConnectPage({
  session,
  onConnected,
}: {
  session: Session | null;
  onConnected: () => Promise<void>;
}) {
  return (
    <>
      <PageHeader
        eyebrow="GitHub connection"
        title="Connect StateForge DPT to GitHub"
        description="Link your GitHub account without leaving this project. You will return to the project overview after authorization."
      />
      <GitHubConnectPanel session={session} onConnected={onConnected} />
    </>
  );
}

function GitHubConnectPanel({
  session,
  onConnected,
}: {
  session: Session | null;
  onConnected: () => Promise<void>;
}) {
  const [authorization, setAuthorization] =
    useState<DeviceAuthorization | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const profileUrl = githubProfileUrl(session);

  useEffect(() => {
    if (!authorization) return;
    let cancelled = false;
    let timer = 0;

    async function poll() {
      try {
        const result = await api<DeviceAuthorizationPoll>(
          `/api/auth/github/device/${authorization?.authorization_id}/poll`,
          { method: "POST" },
        );
        if (cancelled) return;
        if (result.status === "complete") {
          await onConnected();
          return;
        }
        timer = window.setTimeout(
          poll,
          (result.retry_after || authorization?.interval || 5) * 1000,
        );
      } catch (caught) {
        if (cancelled) return;
        setAuthorization(null);
        setError(
          caught instanceof Error
            ? caught.message
            : "GitHub authorization failed",
        );
      }
    }

    timer = window.setTimeout(poll, authorization.interval * 1000);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [authorization, onConnected]);

  async function startAuthentication() {
    setError(null);
    setStarting(true);
    try {
      const value = await api<DeviceAuthorization>(
        "/api/auth/github/device",
        { method: "POST" },
      );
      setAuthorization(value);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "GitHub login could not start",
      );
    } finally {
      setStarting(false);
    }
  }

  return (
    <section className="github-connect-grid">
      <article className="panel github-connect-card">
        <span className="github-connect-icon"><Github /></span>
        {profileUrl ? (
          <>
            <span className="eyebrow">Connected account</span>
            <h2>{session?.user?.login}</h2>
            <p>DPT is connected and ready to reconcile this project with GitHub.</p>
            <a
              className="button button-primary"
              href={profileUrl}
              rel="noreferrer"
              target="_blank"
            >
              Open GitHub profile<ExternalLink />
            </a>
          </>
        ) : authorization ? (
          <div className="github-connect-authorization">
            <span className="eyebrow">Authorize this installation</span>
            <h2>Enter your one-time code on GitHub</h2>
            <button
              className="device-code"
              onClick={() => navigator.clipboard.writeText(authorization.user_code)}
              type="button"
            >
              {authorization.user_code}
              <small>Click to copy</small>
            </button>
            <p>Open GitHub, enter the code, and approve the requested repository access.</p>
            <a
              className="button button-github"
              href={authorization.verification_uri}
              rel="noreferrer"
              target="_blank"
            >
              <Github />Open GitHub authorization<ExternalLink />
            </a>
            <span className="device-waiting">
              <RefreshCw />Waiting for GitHub confirmation…
            </span>
          </div>
        ) : (
          <>
            <span className="eyebrow">GitHub account</span>
            <h2>Bring trusted delivery evidence into DPT</h2>
            <p>Connect to reconcile issues, pull requests, reviews, labels, branches, and merge status with the declared roadmap.</p>
            {error && <ErrorBanner message={error} />}
            <button
              className="button button-github"
              disabled={starting}
              onClick={startAuthentication}
              type="button"
            >
              <Github />{starting ? "Connecting…" : "Connect with GitHub"}
            </button>
          </>
        )}
      </article>
      <aside className="panel github-connect-details">
        <span className="eyebrow">What this enables</span>
        <h2>GitHub-backed progress tracking</h2>
        <ul>
          <li><CheckCircle2 /><span><strong>Reconcile delivery</strong>Match labelled issues and pull requests to DPT steps.</span></li>
          <li><GitPullRequest /><span><strong>Manage workflow</strong>Open planned work and pull requests when you choose.</span></li>
          <li><ShieldCheck /><span><strong>Trust reviewed work</strong>Use merged pull requests as completion evidence.</span></li>
        </ul>
        <p><LockKeyhole />Your authorization is stored only by this local DPT installation.</p>
      </aside>
    </section>
  );
}

function CreateProjectModal({ onClose, onCreated }: { onClose: () => void; onCreated: (project: Project) => void }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    const data = new FormData(event.currentTarget);
    try {
      const project = await api<Project>("/api/projects", {
        method: "POST",
        body: JSON.stringify({
          name: data.get("name"),
          slug: data.get("slug"),
          release: data.get("release"),
          plan_source_branch: data.get("plan_source_branch"),
        }),
      });
      onCreated(project);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Project could not be created");
      setSaving(false);
    }
  }
  return (
    <Modal title="Create a DPT project" description="Start with the project identity. Repositories come next." onClose={onClose}>
      <form className="form-stack" onSubmit={submit}>
        {error && <ErrorBanner message={error} />}
        <label><span>Project name</span><input name="name" placeholder="StateForge" required /></label>
        <label><span>Project slug</span><input name="slug" pattern="[a-z][a-z0-9-]*" placeholder="stateforge" required /></label>
        <label><span>Current release</span><input defaultValue="MVP" name="release" required /></label>
        <label><span>Plan source branch</span><input defaultValue="mvp/implementation" name="plan_source_branch" required /><small>Reconciliation reads the portable plan from this parent branch without changing your checkout.</small></label>
        <div className="modal-actions"><button className="button button-ghost" onClick={onClose} type="button">Cancel</button><button className="button button-primary" disabled={saving}>{saving ? "Creating…" : "Create project"}</button></div>
      </form>
    </Modal>
  );
}

function ImportProjectModal({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: (project: Project) => void;
}) {
  const [localPath, setLocalPath] = useState("");
  const [inspection, setInspection] = useState<ProjectImportInspection | null>(null);
  const [inspecting, setInspecting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function inspect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setInspecting(true);
    setError(null);
    try {
      const result = await api<ProjectImportInspection>("/api/projects/import/inspect", {
        method: "POST",
        body: JSON.stringify({ local_path: localPath.trim() }),
      });
      setInspection(result);
    } catch (caught) {
      setInspection(null);
      setError(caught instanceof Error ? caught.message : "The project could not be inspected");
    } finally {
      setInspecting(false);
    }
  }

  async function importProject() {
    setImporting(true);
    setError(null);
    try {
      const dashboard = await api<Dashboard>("/api/projects/import", {
        method: "POST",
        body: JSON.stringify({ local_path: localPath.trim() }),
      });
      onImported(dashboard.project);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The project could not be imported");
      setImporting(false);
    }
  }

  return (
    <Modal
      description="Choose any local Git clone that contains a published STATEFORGE_DPT.md. DPT verifies the repository and shows the complete plan before creating anything locally."
      onClose={onClose}
      title="Import a project from Git"
      wide
    >
      <form className="form-stack project-import-form" onSubmit={inspect}>
        {error && <ErrorBanner message={error} />}
        <label>
          <span>Local Git repository</span>
          <div className="project-import-path">
            <FolderGit2 />
            <input
              autoFocus
              onChange={(event) => {
                setLocalPath(event.target.value);
                setInspection(null);
                setError(null);
              }}
              placeholder="/Users/you/Projects/stateforge-protocol"
              required
              value={localPath}
            />
          </div>
          <small>The clone's origin must match one of the repositories declared by the embedded DPT plan.</small>
        </label>

        {inspection && (
          <section className="project-import-preview">
            <header>
              <span className="project-import-icon"><Layers3 /></span>
              <div>
                <span className="eyebrow">Verified portable plan</span>
                <h3>{inspection.project.name}</h3>
                <p>{inspection.project.release} · Revision {inspection.project.revision}</p>
              </div>
              <CheckCircle2 />
            </header>
            <div className="project-import-metrics">
              <span><strong>{inspection.repositories}</strong><small>repositories</small></span>
              <span><strong>{inspection.steps}</strong><small>milestones</small></span>
              <span><strong>{inspection.fingerprint.slice(0, 10)}</strong><small>verified fingerprint</small></span>
            </div>
            <div className="project-import-source">
              <GitBranch />
              <span><small>Importing from</small><strong>{inspection.source_repository}</strong><code>{inspection.source_path}</code></span>
            </div>
            <p className="project-import-note">
              This clone will be attached now. Every other repository, milestone, branch, dependency, and ordering rule will be recreated and ready to clone or attach on this computer. GitHub delivery activity returns after reconciliation.
            </p>
          </section>
        )}

        <div className="modal-actions">
          <button className="button button-ghost" disabled={inspecting || importing} onClick={onClose} type="button">Cancel</button>
          {!inspection ? (
            <button className="button button-primary" disabled={inspecting || !localPath.trim()}>
              <FolderInput />{inspecting ? "Verifying plan…" : "Inspect project"}
            </button>
          ) : (
            <button className="button button-primary" disabled={importing} onClick={importProject} type="button">
              <FolderInput />{importing ? "Importing project…" : "Import complete project"}
            </button>
          )}
        </div>
      </form>
    </Modal>
  );
}

function Sidebar({
  collapsed,
  page,
  project,
  projects,
  session,
  onAddRepository,
  onConnectGitHub,
  onToggleCollapsed,
}: {
  collapsed: boolean;
  page: PageName;
  project?: Project;
  projects: Project[];
  session: Session | null;
  onAddRepository: () => void;
  onConnectGitHub: () => void;
  onToggleCollapsed: () => void;
}) {
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const switcher = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!switcherOpen) return;

    function closeOnOutsideClick(event: MouseEvent) {
      if (!switcher.current?.contains(event.target as Node)) {
        setSwitcherOpen(false);
      }
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setSwitcherOpen(false);
    }

    document.addEventListener("mousedown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [switcherOpen]);

  function selectProject(projectId: string) {
    setSwitcherOpen(false);
    navigate(`/projects/${projectId}/overview`);
  }

  const profileUrl = githubProfileUrl(session);

  return (
    <aside className={collapsed ? "sidebar collapsed" : "sidebar"}>
      <div className="sidebar-controls">
        <button aria-label="All projects" className="sidebar-projects" onClick={() => navigate("/projects")} title={collapsed ? "All projects" : undefined}><ArrowLeft /><span>All projects</span></button>
        <button aria-label={collapsed ? "Expand navigation" : "Collapse navigation"} className="sidebar-collapse" onClick={onToggleCollapsed} title={collapsed ? "Expand navigation" : "Collapse navigation"} type="button">
          {collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
        </button>
      </div>
      <StateForgeLogo />
      <div className="project-switcher-container" ref={switcher}>
        <button
          aria-label={`Switch project. Current project: ${project?.name || "Loading"}`}
          aria-expanded={switcherOpen}
          aria-haspopup="menu"
          className="project-switcher"
          onClick={() => setSwitcherOpen((open) => !open)}
          type="button"
        >
          <span className="project-avatar">{project?.name.slice(0, 2).toUpperCase()}</span>
          <span className="project-switcher-copy"><small>Current project</small><strong>{project?.name || "Loading"}</strong></span>
          <ChevronDown className={switcherOpen ? "switcher-chevron open" : "switcher-chevron"} />
        </button>
        {switcherOpen && (
          <div className="project-switcher-menu" role="menu">
            <span className="project-switcher-heading">Switch project</span>
            {projects.map((candidate) => (
              <button
                className={candidate.project_id === project?.project_id ? "active" : ""}
                key={candidate.project_id}
                onClick={() => selectProject(candidate.project_id)}
                role="menuitem"
                type="button"
              >
                <span className="project-avatar">{candidate.name.slice(0, 2).toUpperCase()}</span>
                <span><strong>{candidate.name}</strong><small>{candidate.release}</small></span>
                {candidate.project_id === project?.project_id && <CheckCircle2 />}
              </button>
            ))}
            <button className="project-switcher-all" onClick={() => navigate("/projects")} role="menuitem" type="button"><Layers3 />View all projects<ArrowRight /></button>
          </div>
        )}
      </div>
      <nav>
        <span className="nav-heading">Project</span>
        {pageItems.map((item) => (
          <button aria-label={item.label} className={item.page === page ? "active" : ""} key={item.page} onClick={() => navigate(`/projects/${project?.project_id}/${item.page}`)} title={collapsed ? item.label : undefined}>{item.icon}<span>{item.label}</span>{item.page === "reconciliation" && <i />}</button>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <button aria-label="Add repository" className="button button-sidebar" onClick={onAddRepository} title={collapsed ? "Add repository" : undefined}><Plus /><span>Add repository</span></button>
        {profileUrl ? (
          <a
            aria-label={`Open ${session?.user?.login} on GitHub`}
            className="sidebar-account"
            href={profileUrl}
            rel="noreferrer"
            target="_blank"
            title={collapsed ? "Open GitHub profile" : undefined}
          >
            {session?.user?.avatar_url ? <img src={session.user.avatar_url} alt="" /> : <span><Github /></span>}
            <div><strong>{session?.user?.login}</strong><small>Open GitHub profile</small></div>
          </a>
        ) : (
          <button
            aria-label="Connect to GitHub"
            className="sidebar-account"
            onClick={onConnectGitHub}
            title={collapsed ? "Connect to GitHub" : undefined}
            type="button"
          >
            <span><Github /></span>
            <div><strong>Local mode</strong><small>Connect to GitHub</small></div>
          </button>
        )}
      </div>
    </aside>
  );
}

function Topbar({ project, session, onConnectGitHub, onReconcile, reconciling }: { project?: Project; session: Session | null; onConnectGitHub: () => void; onReconcile: () => Promise<void>; reconciling: boolean }) {
  const profileUrl = githubProfileUrl(session);

  return (
    <header className="topbar">
      <div className="mobile-logo"><PanelLeft /><StateForgeLogo compact /></div>
      <div className="breadcrumb"><span>{project?.release || "MVP"}</span><ChevronRight /><strong>{project?.name || "Project"}</strong></div>
      <div className="topbar-actions">
        {profileUrl ? (
          <a className="connection connected" href={profileUrl} rel="noreferrer" target="_blank"><i />GitHub connected</a>
        ) : (
          <button className="connection" onClick={onConnectGitHub} type="button"><i />Connect GitHub</button>
        )}
        <button className="button button-secondary" disabled={reconciling} onClick={onReconcile}><RefreshCw className={reconciling ? "spin-icon" : undefined} />{reconciling ? "Reconciling…" : "Reconcile"}</button>
      </div>
    </header>
  );
}

function CurrentPage(props: {
  page: PageName;
  dashboard: Dashboard;
  activity: Activity[];
  session: Session | null;
  onAddRepository: () => void;
  onOpenBlocker: (blocker: Blocker) => void;
  onOpenStep: (step: Step) => void;
  onGenerate: () => Promise<boolean>;
  onPublishGit: () => Promise<boolean>;
  onGitHubConnected: () => Promise<void>;
  onReconcile: () => Promise<void>;
  onRemoveRepository: () => Promise<void>;
  onPlanChanged: () => Promise<void>;
  reconciliationProgress: ReconciliationProgress | null;
  reconciling: boolean;
}) {
  switch (props.page) {
    case "roadmap": return <RoadmapPage dashboard={props.dashboard} onOpenBlocker={props.onOpenBlocker} onOpenStep={props.onOpenStep} />;
    case "work": return <WorkPage dashboard={props.dashboard} onOpenBlocker={props.onOpenBlocker} onOpenStep={props.onOpenStep} />;
    case "repositories": return <RepositoriesPage dashboard={props.dashboard} onAdd={props.onAddRepository} onRemoved={props.onRemoveRepository} />;
    case "plan": return <PlanEditorPage canPublishGit={Boolean(props.session?.authenticated)} dashboard={props.dashboard} onChanged={props.onPlanChanged} onGenerate={props.onGenerate} onPublishGit={props.onPublishGit} onOpenDelivery={props.onOpenStep} />;
    case "reconciliation": return <ReconciliationPage dashboard={props.dashboard} onOpenStep={props.onOpenStep} onReconcile={props.onReconcile} progress={props.reconciliationProgress} reconciling={props.reconciling} session={props.session} />;
    case "activity": return <ActivityPage activity={props.activity} />;
    case "settings": return <SettingsPage dashboard={props.dashboard} onChanged={props.onPlanChanged} />;
    case "connect-github": return <GitHubConnectPage session={props.session} onConnected={props.onGitHubConnected} />;
    default: return <OverviewPage dashboard={props.dashboard} onAddRepository={props.onAddRepository} onOpenStep={props.onOpenStep} onReconcile={props.onReconcile} />;
  }
}

function OverviewPage({ dashboard, onAddRepository, onOpenStep, onReconcile }: { dashboard: Dashboard; onAddRepository: () => void; onOpenStep: (step: Step) => void; onReconcile: () => void }) {
  const total = dashboard.steps.length;
  const completed = dashboard.metrics.completed || 0;
  const progress = total ? Math.round((completed / total) * 100) : 0;
  const ready = dashboard.steps.filter((step) => step.dependencies_complete && (step.computed_state === "ready" || step.computed_state === "pending")).slice(0, 4);
  const review = dashboard.steps.filter((step) => step.computed_state === "in_review").slice(0, 4);
  return (
    <>
      <PageHeader eyebrow="Project overview" title={`${dashboard.project.name} ${dashboard.project.release}`} description="The canonical project plan and current GitHub delivery evidence." actions={<button className="button button-primary" onClick={onReconcile}><RefreshCw />Reconcile GitHub</button>} />
      <section className="metric-grid">
        <MetricCard label="Trusted complete" value={`${completed}/${total}`} detail={`${progress}% of the current release`} tone="blue" icon={<ShieldCheck />} />
        <MetricCard label="Ready to build" value={String(dashboard.metrics.actionable)} detail="All prerequisites satisfied" tone="green" icon={<Sparkles />} />
        <MetricCard label="In review" value={String(dashboard.metrics.in_review)} detail="Open pull requests" tone="purple" icon={<GitPullRequest />} />
        <MetricCard label="Active blockers" value={String(dashboard.metrics.active_blockers)} detail={`${dashboard.metrics.blocked_milestones} milestones affected`} tone="red" icon={<Bug />} />
        <MetricCard label="Reconciliation findings" value={String(dashboard.metrics.findings)} detail="Workflow rules requiring attention" tone="orange" icon={<FileDiff />} />
      </section>
      <section className="overview-grid">
        <ReleaseProgressMap dashboard={dashboard} onOpenStep={onOpenStep} />
        <article className="panel"><PanelTitle title="Ready now" meta={`${ready.length} shown`} />{ready.length ? <div className="compact-list">{ready.map((step) => <StepRow key={step.step_id} step={step} onClick={() => onOpenStep(step)} />)}</div> : <EmptyMini title="No ready work" text="Complete an upstream dependency to unlock the next step." />}</article>
        <article className="panel"><PanelTitle title="Active reviews" meta={`${review.length} shown`} />{review.length ? <div className="compact-list">{review.map((step) => <StepRow key={step.step_id} step={step} onClick={() => onOpenStep(step)} />)}</div> : <EmptyMini title="No pull requests in review" text="GitHub reconciliation will place open PRs here." />}</article>
        <article className="panel repo-health"><PanelTitle title="Repository health" meta={`${dashboard.repositories.length} managed`} />{dashboard.repositories.length ? dashboard.repositories.map((repository) => <RepositoryHealth key={repository.repository_id} repository={repository} />) : <EmptyMini title="No repositories connected" text="Add the repositories that make up this project." action={<button className="button button-secondary button-small" onClick={onAddRepository}><Plus />Add repository</button>} />}</article>
      </section>
    </>
  );
}

function MetricCard({ label, value, detail, tone, icon }: { label: string; value: string; detail: string; tone: string; icon: ReactNode }) {
  return <article className={`metric-card metric-${tone}`}><span className="metric-icon">{icon}</span><div><p>{label}</p><strong>{value}</strong><span>{detail}</span></div></article>;
}

function PanelTitle({ title, meta }: { title: string; meta?: string }) {
  return <header className="panel-title"><h2>{title}</h2>{meta && <span>{meta}</span>}</header>;
}

function EmptyMini({ title, text, action }: { title: string; text: string; action?: ReactNode }) {
  return <div className="empty-mini"><CircleDot /><div><strong>{title}</strong><p>{text}</p>{action}</div></div>;
}

function StepRow({ step, onClick }: { step: Step; onClick: () => void }) {
  return <button className="step-row" onClick={onClick}><code>{step.step_id}</code><div><strong>{step.title}</strong><span>{step.repository_alias}</span></div><ContextStatusPill step={step} /><StatusBadge status={step.computed_state} /><ChevronRight /></button>;
}

function ContextStatusPill({ step }: { step: Step }) {
  return <span className={`context-status-pill context-status-${step.context_readiness.state}`} title={step.context_readiness.reasons.join(" · ") || "Implementation context is approved"}><ShieldCheck />{step.context_readiness.ready ? "Context ready" : step.context_readiness.state.replaceAll("_", " ")}</span>;
}

function RepositoryHealth({ repository }: { repository: Repository }) {
  return <div className="repo-health-row"><span className={repository.clone_state ? "repo-health-icon healthy" : "repo-health-icon"}><FolderGit2 /></span><div><strong>{repository.github_full_name}</strong><span>{repository.clone_state ? `${repository.clone_state.branch} · ${repository.clone_state.dirty ? "uncommitted changes" : "clean"}` : "No local clone attached"}</span></div><span className={repository.clone_state ? "health-label healthy" : "health-label"}>{repository.clone_state ? "Attached" : "Setup"}</span></div>;
}

function RoadmapPage({ dashboard, onOpenBlocker, onOpenStep }: { dashboard: Dashboard; onOpenBlocker: (blocker: Blocker) => void; onOpenStep: (step: Step) => void }) {
  const [selectedRepositories, setSelectedRepositories] = useState<string[]>(dashboard.repositories.map((repo) => repo.repository_id));
  const [query, setQuery] = useState("");
  const [view, setView] = useState<"stages" | "graph">("stages");
  const visible = dashboard.steps.filter((step) => selectedRepositories.includes(step.repository_id) && (!query || `${step.step_id} ${step.title}`.toLowerCase().includes(query.toLowerCase())));
  const visibleBlockers = dashboard.blockers.filter((blocker) => selectedRepositories.includes(blocker.repository_id) && (!query || `${blocker.github_full_name} ${blocker.number} ${blocker.title}`.toLowerCase().includes(query.toLowerCase())));
  const layers = dependencyLayers(visible);
  const blockersByLevel = groupBlockersByPlanLevel(visibleBlockers, dependencyLayers(dashboard.steps));
  function toggle(id: string) { setSelectedRepositories((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id]); }
  return (
    <>
      <PageHeader eyebrow="Complete project" title="Cross-repository roadmap" description="Read the implementation from foundations to the final integrated runtime." />
      <section className="toolbar">
        <SearchInput value={query} onChange={setQuery} placeholder="Search roadmap" />
        <div className="roadmap-toolbar-actions">
          <div aria-label="Roadmap view" className="view-switcher" role="group">
            <button aria-pressed={view === "stages"} className={view === "stages" ? "active" : ""} onClick={() => setView("stages")}><Layers3 />Stages</button>
            <button aria-pressed={view === "graph"} className={view === "graph" ? "active" : ""} onClick={() => setView("graph")}><Network />Graph</button>
          </div>
          <div className="repo-chips"><button className={selectedRepositories.length === dashboard.repositories.length ? "active" : ""} onClick={() => setSelectedRepositories(dashboard.repositories.map((repo) => repo.repository_id))}>All repositories</button>{dashboard.repositories.map((repo) => <button className={selectedRepositories.includes(repo.repository_id) ? "active" : ""} key={repo.repository_id} onClick={() => toggle(repo.repository_id)}><i />{repo.alias}</button>)}</div>
        </div>
      </section>
      <section className="roadmap-canvas">
        <div className="roadmap-key"><span><i className="key-completed" />Completed</span><span><i className="key-ready" />Ready</span><span><i className="key-review" />In review</span><span><i className="key-blocked" />Blocked</span><span><i className="key-github-blocker" />GitHub blocker</span></div>
        {layers.length || visibleBlockers.length ? view === "graph" ? <DependencyGraph blockers={visibleBlockers} steps={visible} onOpenBlocker={onOpenBlocker} onOpenStep={onOpenStep} /> : <div className="roadmap-layers">{layers.flatMap((layer, index) => {
          const levelBlockers = blockersByLevel.get(index) || [];
          return [
            ...(levelBlockers.length ? [<RoadmapBlockerColumn blockers={levelBlockers} key={`blockers-${index}`} level={index} onOpen={onOpenBlocker} />] : []),
            <div className="roadmap-stage" key={`steps-${index}`}><div className="stage-label"><span>{String(index + 1).padStart(2, "0")}</span><small>{index === 0 ? "Foundation" : `Dependency level ${index}`}</small></div><div className="stage-steps">{layer.map((step) => <button className={`roadmap-node node-${step.computed_state}`} key={step.step_id} onClick={() => onOpenStep(step)}><header><code>{step.step_id}</code><StatusBadge status={step.computed_state} /></header><strong>{step.title}</strong><ContextStatusPill step={step} /><footer><span>{step.repository_alias}</span><span>{step.blockers.some((blocker) => blocker.is_active) ? `${step.blockers.filter((blocker) => blocker.is_active).length} GitHub blocker${step.blockers.filter((blocker) => blocker.is_active).length === 1 ? "" : "s"}` : step.depends_on.length ? `After ${step.depends_on.join(", ")}` : "No dependencies"}</span></footer></button>)}</div>{index < layers.length - 1 && <div className="roadmap-arrow"><ArrowRight /></div>}</div>,
          ];
        })}</div> : <EmptyState icon={<Network />} title="Nothing matches this view" description="Select at least one repository or clear the search." />}
      </section>
    </>
  );
}

function RoadmapBlockerColumn({ blockers, level, onOpen }: { blockers: Blocker[]; level: number; onOpen: (blocker: Blocker) => void }) {
  return (
    <div className="roadmap-stage roadmap-blocker-stage">
      <div className="stage-label blocker-stage-label"><span>{level === 0 ? "BF" : `B${level}`}</span><small>{level === 0 ? "Foundation blockers" : `Blocker level ${level}`}</small></div>
      <div className="stage-steps">
        {blockers.map((blocker) => (
          <button className={`roadmap-blocker-card roadmap-blocker-card-${blocker.status}`} key={blocker.blocker_id} onClick={() => onOpen(blocker)} type="button">
            <header><code>{blocker.repository_alias} #{blocker.number}</code><span className={`blocker-node-status blocker-node-status-${blocker.status}`}>{blocker.status}</span></header>
            <strong>{blocker.title}</strong>
            <footer><span><Bug />Blocks {blocker.blocked_step_ids.join(", ")}</span><ChevronRight /></footer>
          </button>
        ))}
      </div>
      <div className="roadmap-arrow blocker-column-arrow"><ArrowRight /></div>
    </div>
  );
}

function DependencyGraph({ blockers, steps, onOpenBlocker, onOpenStep }: { blockers: Blocker[]; steps: Step[]; onOpenBlocker: (blocker: Blocker) => void; onOpenStep: (step: Step) => void }) {
  const [focusedStepId, setFocusedStepId] = useState<string | null>(null);
  const nodeWidth = 232;
  const nodeHeight = 96;
  const columnGap = 112;
  const rowGap = 28;
  const paddingX = 38;
  const paddingY = 58;
  const layers = dependencyLayers(steps);
  const blockersByLevel = groupBlockersByPlanLevel(blockers, layers);
  const columns: Array<
    | { kind: "blockers"; level: number; blockers: Blocker[] }
    | { kind: "steps"; level: number; steps: Step[] }
  > = [];
  layers.forEach((layer, level) => {
    const levelBlockers = blockersByLevel.get(level) || [];
    if (levelBlockers.length) columns.push({ kind: "blockers", level, blockers: levelBlockers });
    columns.push({ kind: "steps", level, steps: layer });
  });
  const rows = Math.max(1, ...columns.map((column) => column.kind === "steps" ? column.steps.length : column.blockers.length));
  const width = Math.max(980, paddingX * 2 + columns.length * nodeWidth + Math.max(0, columns.length - 1) * columnGap);
  const height = Math.max(500, paddingY + 30 + rows * nodeHeight + Math.max(0, rows - 1) * rowGap);
  const nodes = columns.flatMap((column, columnIndex) => column.kind === "steps" ? column.steps.map((step, row) => ({
      step,
      x: paddingX + columnIndex * (nodeWidth + columnGap),
      y: paddingY + row * (nodeHeight + rowGap),
    })) : []);
  const blockerNodes = columns.flatMap((column, columnIndex) => column.kind === "blockers" ? column.blockers.map((blocker, row) => ({
      blocker,
      x: paddingX + columnIndex * (nodeWidth + columnGap),
      y: paddingY + row * (nodeHeight + rowGap),
    })) : []);
  const positions = new Map(nodes.map((node) => [node.step.step_id, node]));
  const edges = nodes.flatMap((target) => target.step.depends_on.flatMap((dependencyId) => {
    const source = positions.get(dependencyId);
    if (!source) return [];
    const startX = source.x + nodeWidth;
    const startY = source.y + nodeHeight / 2;
    const endX = target.x;
    const endY = target.y + nodeHeight / 2;
    const bend = Math.max(42, (endX - startX) / 2);
    return [{
      id: `${dependencyId}-${target.step.step_id}`,
      sourceId: dependencyId,
      targetId: target.step.step_id,
      path: `M ${startX} ${startY} C ${startX + bend} ${startY}, ${endX - bend} ${endY}, ${endX} ${endY}`,
    }];
  }));
  const relatedStepIds = new Set<string>();
  const blockerEdges = blockerNodes.flatMap(({ blocker, x, y }) =>
    blocker.blocked_step_ids.flatMap((stepId) => {
      const target = positions.get(stepId);
      if (!target) return [];
      const startX = x + nodeWidth;
      const startY = y + nodeHeight / 2;
      const endX = target.x;
      const endY = target.y + nodeHeight / 2;
      const bend = Math.max(42, (endX - startX) / 2);
      return [{
        id: `${blocker.blocker_id}-${stepId}`,
        status: blocker.status,
        path: `M ${startX} ${startY} C ${startX + bend} ${startY}, ${endX - bend} ${endY}, ${endX} ${endY}`,
      }];
    }),
  );
  if (focusedStepId) {
    relatedStepIds.add(focusedStepId);
    for (const edge of edges) {
      if (edge.sourceId === focusedStepId || edge.targetId === focusedStepId) {
        relatedStepIds.add(edge.sourceId);
        relatedStepIds.add(edge.targetId);
      }
    }
  }

  return (
    <div className="dependency-graph" style={{ height, width }}>
      {columns.map((column, index) => column.kind === "blockers" ? <div className="dependency-level dependency-blocker-level" key={`blockers-${column.level}`} style={{ left: paddingX + index * (nodeWidth + columnGap), top: 16 }}><Bug /><span>{column.level === 0 ? "BF" : `B${column.level}`}</span>{column.level === 0 ? "Foundation blockers" : `Blocker level ${column.level}`}</div> : <div className="dependency-level" key={`steps-${column.level}`} style={{ left: paddingX + index * (nodeWidth + columnGap), top: 16 }}><span>{String(column.level + 1).padStart(2, "0")}</span>{column.level === 0 ? "Foundation" : `Dependency level ${column.level}`}</div>)}
      <svg aria-hidden="true" className="dependency-edges" height={height} width={width}>
        <defs><marker id="dependency-arrow" markerHeight="8" markerWidth="8" orient="auto" refX="7" refY="4"><path d="M 0 0 L 8 4 L 0 8 z" /></marker><marker id="blocker-arrow-active" markerHeight="8" markerWidth="8" orient="auto" refX="7" refY="4"><path d="M 0 0 L 8 4 L 0 8 z" /></marker><marker id="blocker-arrow-resolved" markerHeight="8" markerWidth="8" orient="auto" refX="7" refY="4"><path d="M 0 0 L 8 4 L 0 8 z" /></marker><marker id="blocker-arrow-dismissed" markerHeight="8" markerWidth="8" orient="auto" refX="7" refY="4"><path d="M 0 0 L 8 4 L 0 8 z" /></marker></defs>
        {edges.map((edge) => <path className={focusedStepId ? edge.sourceId === focusedStepId || edge.targetId === focusedStepId ? "active" : "muted" : ""} d={edge.path} key={edge.id} markerEnd="url(#dependency-arrow)" />)}
        {blockerEdges.map((edge) => <path className={`blocker-edge blocker-edge-${edge.status}`} d={edge.path} key={edge.id} markerEnd={`url(#blocker-arrow-${edge.status})`} />)}
      </svg>
      {blockerNodes.map(({ blocker, x, y }) => (
        <button className={`dependency-node dependency-blocker-node dependency-blocker-node-${blocker.status}`} key={blocker.blocker_id} onClick={() => onOpenBlocker(blocker)} style={{ left: x, top: y }} type="button">
          <header><code>{blocker.repository_alias} #{blocker.number}</code><span><i />{blocker.status} blocker</span></header>
          <strong>{blocker.title}</strong>
          <footer>Blocks {blocker.blocked_step_ids.join(", ")}</footer>
        </button>
      ))}
      {nodes.map(({ step, x, y }) => (
        <button
          aria-label={`${step.step_id}: ${step.title}, ${step.computed_state.replace("_", " ")}`}
          className={`dependency-node node-${step.computed_state}${focusedStepId && !relatedStepIds.has(step.step_id) ? " muted" : ""}`}
          key={step.step_id}
          onBlur={() => setFocusedStepId(null)}
          onClick={() => onOpenStep(step)}
          onFocus={() => setFocusedStepId(step.step_id)}
          onMouseEnter={() => setFocusedStepId(step.step_id)}
          onMouseLeave={() => setFocusedStepId(null)}
          style={{ left: x, top: y }}
        >
          <header><code>{step.step_id}</code><span><i />{step.computed_state.replace("_", " ")}</span></header>
          <strong>{step.title}</strong>
          <footer>{step.repository_alias}</footer>
        </button>
      ))}
    </div>
  );
}

function dependencyLayers(steps: Step[]) {
  const ids = new Set(steps.map((step) => step.step_id));
  const memo = new Map<string, number>();
  function depth(step: Step, visiting = new Set<string>()): number {
    if (memo.has(step.step_id)) return memo.get(step.step_id)!;
    if (visiting.has(step.step_id)) return 0;
    visiting.add(step.step_id);
    const dependencies = step.depends_on.map((id) => steps.find((candidate) => candidate.step_id === id)).filter((value): value is Step => Boolean(value && ids.has(value.step_id)));
    const value = dependencies.length ? Math.max(...dependencies.map((dependency) => depth(dependency, visiting))) + 1 : 0;
    memo.set(step.step_id, value);
    return value;
  }
  const layers: Step[][] = [];
  for (const step of steps) (layers[depth(step)] ||= []).push(step);
  return layers;
}

function groupBlockersByPlanLevel(blockers: Blocker[], layers: Step[][]) {
  const levelByStep = new Map(
    layers.flatMap((layer, level) => layer.map((step) => [step.step_id, level] as const)),
  );
  const grouped = new Map<number, Blocker[]>();
  for (const blocker of blockers) {
    const affectedLevels = blocker.blocked_step_ids
      .map((stepId) => levelByStep.get(stepId))
      .filter((level): level is number => level !== undefined);
    // A blocker belongs immediately before the earliest plan level it can stop.
    // Additional edges still show every later milestone affected by the same issue.
    const level = affectedLevels.length ? Math.min(...affectedLevels) : 0;
    const group = grouped.get(level) || [];
    group.push(blocker);
    grouped.set(level, group);
  }
  return grouped;
}

function stepAssignees(step: Step) {
  const accounts = new Map<string, GitHubAccount>();
  for (const artifact of step.artifacts) {
    for (const account of artifact.assignees) {
      accounts.set(account.login.toLowerCase(), account);
    }
  }
  return [...accounts.values()];
}

function AssigneeList({ accounts }: { accounts: GitHubAccount[] }) {
  const visibleAccounts = accounts.slice(0, 3);
  const names = accounts.map((account) => account.login).join(", ");

  return (
    <span className={accounts.length ? "assignee-list" : "assignee-list unassigned"}>
      <span className="assignee-avatars" aria-hidden="true">
        {visibleAccounts.map((account) => (
          account.avatar_url ? (
            <img key={account.login} src={account.avatar_url} alt="" />
          ) : (
            <span className="assignee-avatar-fallback" key={account.login}>
              {account.login.slice(0, 1).toUpperCase()}
            </span>
          )
        ))}
        {accounts.length > visibleAccounts.length && (
          <span className="assignee-avatar-more">
            +{accounts.length - visibleAccounts.length}
          </span>
        )}
        {!accounts.length && <span className="assignee-avatar-empty"><Users /></span>}
      </span>
      <span className="assignee-copy">
        <small>Assigned to</small>
        <strong title={names || "No GitHub assignee"}>
          {names || "Unassigned"}
        </strong>
      </span>
    </span>
  );
}

function WorkCard({ step, onOpen }: { step: Step; onOpen: () => void }) {
  const artifact = step.artifacts[0];

  return (
    <button className="work-card" onClick={onOpen} type="button">
      <div>
        <code>{step.step_id}</code>
        <span>{step.repository_alias}</span>
      </div>
      <strong>{step.title}</strong>
      <ContextStatusPill step={step} />
      {step.blockers.some((blocker) => blocker.is_active) && (
        <span className="work-card-blockers">
          <Bug />
          {step.blockers.filter((blocker) => blocker.is_active).length} active blocker{step.blockers.filter((blocker) => blocker.is_active).length === 1 ? "" : "s"}
        </span>
      )}
      <footer>
        <span className="work-card-artifact">
          {artifact ? (
            <><GitPullRequest />#{artifact.number} · {artifact.author_login}</>
          ) : (
            <><CircleDot />No GitHub artifact</>
          )}
        </span>
        <AssigneeList accounts={stepAssignees(step)} />
      </footer>
    </button>
  );
}

function blockerQueueState(blocker: Blocker): StepState {
  if (blocker.status === "dismissed") return "cancelled";
  if (blocker.status === "resolved") return "completed";
  if (blocker.artifacts.some((artifact) => artifact.kind === "pull_request" && artifact.state === "open")) return "in_review";
  return "pending";
}

function WorkBlockerCard({ blocker, onOpen }: { blocker: Blocker; onOpen: () => void }) {
  const pullRequest = blocker.artifacts.find((artifact) => artifact.kind === "pull_request" && artifact.state === "open");
  return (
    <button className={`work-card work-blocker-card work-blocker-card-${blocker.status}`} onClick={onOpen} type="button">
      <div><code><Bug />{blocker.repository_alias} #{blocker.number}</code><span>GitHub blocker</span></div>
      <strong>{blocker.title}</strong>
      <span className="work-blocker-target">Blocks {blocker.blocked_step_ids.join(", ")}</span>
      <footer>
        <span className="work-card-artifact">
          {pullRequest ? <><GitPullRequest />PR #{pullRequest.number} · {pullRequest.author_login}</> : <><CircleDot />Issue #{blocker.number} · {blocker.author_login}</>}
        </span>
        <AssigneeList accounts={blocker.assignees} />
      </footer>
    </button>
  );
}

function WorkPage({ dashboard, onOpenBlocker, onOpenStep }: { dashboard: Dashboard; onOpenBlocker: (blocker: Blocker) => void; onOpenStep: (step: Step) => void }) {
  const [query, setQuery] = useState("");
  const filteredSteps = dashboard.steps.filter((step) => !query || `${step.step_id} ${step.title} ${step.repository_alias}`.toLowerCase().includes(query.toLowerCase()));
  const filteredBlockers = dashboard.blockers.filter((blocker) => !query || `${blocker.github_full_name} ${blocker.number} ${blocker.title} ${blocker.blocked_step_ids.join(" ")}`.toLowerCase().includes(query.toLowerCase()));
  const states: StepState[] = ["ready", "pending", "in_review", "blocked", "completed"];
  if (filteredSteps.some((step) => step.computed_state === "cancelled") || filteredBlockers.some((blocker) => blockerQueueState(blocker) === "cancelled")) states.push("cancelled");
  return (
    <>
      <PageHeader eyebrow="GitHub delivery" title="Work queue" description="Milestones and GitHub blockers organized together by their current delivery state." />
      <section className="toolbar"><SearchInput value={query} onChange={setQuery} placeholder="Search work" /><span className="result-count">{filteredSteps.length + filteredBlockers.length} items</span></section>
      <section className={`kanban kanban-${states.length}-columns`}>
        {states.map((state) => {
          const steps = filteredSteps.filter((step) => step.computed_state === state);
          const blockers = filteredBlockers.filter((blocker) => blockerQueueState(blocker) === state);
          return (
            <div className="kanban-column" key={state}>
              <header>
                <StatusBadge status={state} />
                <span>{steps.length + blockers.length}</span>
              </header>
              <div>
                {blockers.map((blocker) => (
                  <WorkBlockerCard blocker={blocker} key={blocker.blocker_id} onOpen={() => onOpenBlocker(blocker)} />
                ))}
                {steps.map((step) => (
                  <WorkCard
                    key={step.step_id}
                    step={step}
                    onOpen={() => onOpenStep(step)}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </section>
    </>
  );
}

function RepositoriesPage({
  dashboard,
  onAdd,
  onRemoved,
}: {
  dashboard: Dashboard;
  onAdd: () => void;
  onRemoved: () => Promise<void>;
}) {
  const [repositoryToRemove, setRepositoryToRemove] = useState<Repository | null>(null);
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const repositoryStepCount = repositoryToRemove
    ? dashboard.steps.filter(
        (step) => step.repository_id === repositoryToRemove.repository_id,
      ).length
    : 0;

  function requestRemoval(repository: Repository) {
    setRemoveError(null);
    setRepositoryToRemove(repository);
  }

  async function confirmRemoval() {
    if (!repositoryToRemove || repositoryStepCount > 0) return;
    setRemoving(true);
    setRemoveError(null);
    try {
      await api<void>(`/api/repositories/${repositoryToRemove.repository_id}`, {
        method: "DELETE",
      });
      await onRemoved();
      setRepositoryToRemove(null);
    } catch (caught) {
      setRemoveError(
        caught instanceof Error ? caught.message : "Repository could not be removed",
      );
    } finally {
      setRemoving(false);
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Local workspace"
        title="Managed repositories"
        description="Every GitHub repository is bound to a verified local clone before DPT writes the shared plan."
        actions={<button className="button button-primary" onClick={onAdd}><Plus />Add repository</button>}
      />
      {dashboard.repositories.length ? (
        <section className="repository-grid">
          {dashboard.repositories.map((repository) => (
            <RepositoryCard
              key={repository.repository_id}
              repository={repository}
              onRemove={() => requestRemoval(repository)}
            />
          ))}
        </section>
      ) : (
        <EmptyState
          icon={<FolderGit2 />}
          title="No repositories managed"
          description="Choose repositories from GitHub, then attach an existing clone or let DPT clone them."
          action={<button className="button button-primary" onClick={onAdd}><Plus />Add repository</button>}
        />
      )}
      {repositoryToRemove && (
        <Modal
          title="Remove managed repository?"
          description="This changes DPT only."
          onClose={() => !removing && setRepositoryToRemove(null)}
        >
          <div className="repository-removal">
            <span className="repository-removal-icon"><Trash2 /></span>
            <div>
              <strong>{repositoryToRemove.github_full_name}</strong>
              <p>
                DPT will forget this repository, its local-clone association, and
                cached GitHub evidence. The local clone and GitHub repository will
                not be changed.
              </p>
            </div>
          </div>
          {repositoryStepCount > 0 && (
            <ErrorBanner
              message={`This repository owns ${repositoryStepCount} implementation ${repositoryStepCount === 1 ? "step" : "steps"}. It cannot be removed until those steps are reassigned.`}
            />
          )}
          {removeError && <ErrorBanner message={removeError} />}
          <div className="modal-actions">
            <button
              className="button button-ghost"
              disabled={removing}
              onClick={() => setRepositoryToRemove(null)}
              type="button"
            >
              Cancel
            </button>
            <button
              className="button button-danger"
              disabled={removing || repositoryStepCount > 0}
              onClick={confirmRemoval}
              type="button"
            >
              <Trash2 />{removing ? "Removing…" : "Remove from DPT"}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}

function RepositoryCard({
  repository,
  onRemove,
}: {
  repository: Repository;
  onRemove: () => void;
}) {
  const [mode, setMode] = useState<"none" | "attach" | "clone">("none");
  const [message, setMessage] = useState<string | null>(null);
  const [attachPath, setAttachPath] = useState("");
  const [clonePath, setClonePath] = useState("");
  const [loadingSuggestion, setLoadingSuggestion] = useState(false);

  async function selectMode(nextMode: "attach" | "clone") {
    setMode(nextMode);
    setMessage(null);
    if (nextMode !== "clone" || clonePath) return;
    setLoadingSuggestion(true);
    try {
      const suggestion = await api<{ destination_path: string }>(
        `/api/repositories/${repository.repository_id}/clone-suggestion`,
      );
      setClonePath(suggestion.destination_path);
    } catch (caught) {
      setMessage(
        caught instanceof Error
          ? caught.message
          : "The clone destination could not be suggested",
      );
    } finally {
      setLoadingSuggestion(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await api(`/api/repositories/${repository.repository_id}/${mode}`, {
        method: "POST",
        body: JSON.stringify(
          mode === "attach"
            ? { local_path: data.get("path") }
            : { destination_path: data.get("path") },
        ),
      });
      window.location.reload();
    } catch (caught) {
      setMessage(
        caught instanceof Error ? caught.message : "Repository setup failed",
      );
    }
  }
  return <article className="repository-card"><header><span className={repository.clone_state ? "repository-mark connected" : "repository-mark"}><FolderGit2 /></span><div><h2>{repository.github_full_name}</h2><p>{repository.responsibility || "No responsibility description"}</p></div><span className={repository.clone_state ? "health-label healthy" : "health-label"}>{repository.clone_state ? "Connected" : "Needs clone"}</span></header>{repository.clone_state ? <div className="repository-facts"><span><small>Local path</small><code>{repository.clone_state.path}</code></span><span><small>Branch</small><strong><GitBranch />{repository.clone_state.branch}</strong></span><span><small>Working tree</small><strong className={repository.clone_state.dirty ? "warning-text" : "success-text"}>{repository.clone_state.dirty ? "Modified" : "Clean"}</strong></span><span><small>Remote delta</small><strong>{repository.clone_state.ahead} ahead · {repository.clone_state.behind} behind</strong></span></div> : <div className="repository-setup"><p>Tell DPT where the clone already exists, or let DPT suggest an organized workspace location. If an empty project attaches a clone with a portable DPT plan, its complete repository and milestone graph imports automatically.</p><div className="segmented"><button className={mode === "attach" ? "active" : ""} onClick={() => selectMode("attach")}>Use existing clone</button><button className={mode === "clone" ? "active" : ""} onClick={() => selectMode("clone")}>Clone for me</button></div>{mode !== "none" && <form onSubmit={submit}>{message && <ErrorBanner message={message} />}<label><span>{mode === "attach" ? "Repository path" : "Clone destination"}</span><input disabled={loadingSuggestion} name="path" onChange={(event) => mode === "attach" ? setAttachPath(event.target.value) : setClonePath(event.target.value)} placeholder={mode === "attach" ? "/Users/you/Projects/repository" : "Preparing workspace suggestion…"} required value={mode === "attach" ? attachPath : clonePath} />{mode === "clone" && <small>DPT suggests its working directory, grouped by project and repository. You can change it.</small>}</label><button className="button button-primary" disabled={loadingSuggestion || (mode === "clone" && !clonePath)}>{mode === "attach" ? "Validate and attach" : loadingSuggestion ? "Preparing destination…" : "Clone repository"}</button></form>}</div>}<footer><div className="repository-actions"><a href={`https://github.com/${repository.github_full_name}`} target="_blank" rel="noreferrer"><Github />Open on GitHub<ExternalLink /></a><button onClick={onRemove} type="button"><Trash2 />Remove</button></div><span>{repository.last_generated_digest ? `Generated ${repository.last_generated_digest.slice(0, 8)}` : "Plan not generated"}</span></footer></article>;
}

function ReconciliationPage({ dashboard, onOpenStep, onReconcile, progress, reconciling, session }: { dashboard: Dashboard; onOpenStep: (step: Step) => void; onReconcile: () => Promise<void>; progress: ReconciliationProgress | null; reconciling: boolean; session: Session | null }) {
  const findings = dashboard.findings.flatMap((finding) => {
    const step = dashboard.steps.find((candidate) => candidate.step_id === finding.step_id);
    return step ? [{ ...finding, step }] : [];
  });
  const currentProgress = progress || {
    phase: "starting" as const,
    title: "Preparing reconciliation",
    detail: "Preparing the parent-branch plan check and GitHub requests.",
    repository: null,
    repository_index: 0,
    repository_count: dashboard.repositories.length,
    artifacts: 0,
  };
  const activePhase = currentProgress.phase === "updating"
    ? "validating"
    : currentProgress.phase;
  return <>
    <PageHeader
      eyebrow="Plan and evidence"
      title="Reconciliation"
      description={`Fetch the portable plan from origin/${dashboard.project.plan_source_branch}, update the local working copy safely, then validate GitHub delivery evidence.`}
      actions={<button className="button button-primary" disabled={!session?.authenticated || reconciling} onClick={onReconcile}><RefreshCw className={reconciling ? "spin-icon" : undefined} />{reconciling ? "Reconciling…" : "Reconcile now"}</button>}
    />
    {reconciling && (
      <section aria-live="polite" className="reconcile-progress" role="status">
        <span className="reconcile-progress-icon"><RefreshCw /></span>
        <div className="reconcile-progress-copy">
          <strong>{currentProgress.title}</strong>
          <p>{currentProgress.detail}</p>
          <div className="reconcile-progress-context">
            {currentProgress.repository && <code>{currentProgress.repository}</code>}
            <span>{currentProgress.repository_index} of {currentProgress.repository_count} repositories</span>
            <span>{currentProgress.artifacts} GitHub items found</span>
          </div>
        </div>
        <div aria-hidden="true" className="reconcile-progress-phases">
          <span className={activePhase === "starting" ? "active" : ""}><i />Prepare</span>
          <span className={activePhase === "plan_fetching" ? "active" : ""}><i />Fetch plan</span>
          <span className={activePhase === "plan_applying" ? "active" : ""}><i />Compare plan</span>
          <span className={activePhase === "fetching" ? "active" : ""}><i />Fetch GitHub</span>
          <span className={activePhase === "caching" ? "active" : ""}><i />Save evidence</span>
          <span className={activePhase === "dependencies" ? "active" : ""}><i />Resolve blockers</span>
          <span className={activePhase === "validating" ? "active" : ""}><i />Apply rules</span>
        </div>
        <div aria-hidden="true" className="reconcile-progress-track"><i /></div>
      </section>
    )}
    <div className={reconciling ? "reconcile-results is-refreshing" : "reconcile-results"}>
      <section className="reconcile-summary"><div className={findings.length ? "reconcile-score warning" : "reconcile-score"}><ShieldCheck /><strong>{findings.length ? `${findings.length} findings` : "In sync"}</strong><span>{session?.authenticated ? "Latest GitHub evidence is cached locally." : "Connect GitHub to retrieve delivery evidence."}</span></div><div className="reconcile-rules"><span><i />Open issue → Pending</span><span><i />Open PR → In review</span><span><i />Open blocker → Blocked</span><span><i />Merged PR → Completed</span><span><i />Closed unmerged PR → Cancelled</span></div></section>
      {findings.length ? <section className="finding-list">{findings.map((finding, index) => <article key={`${finding.step.step_id}-${index}`}><span className={`finding-icon ${finding.severity}`}><CircleAlert /></span><div><header><strong>{finding.kind}</strong><code>{finding.step.step_id}</code></header><p>{finding.message}</p><span>{finding.step.repository_alias} · {finding.step.title}</span></div><button className="button button-secondary button-small" disabled={reconciling} onClick={() => onOpenStep(finding.step)}>Review</button></article>)}</section> : <EmptyState icon={<ShieldCheck />} title="No reconciliation findings" description="GitHub evidence follows the current DPT plan and dependency rules." />}
    </div>
  </>;
}

function ActivityPage({ activity }: { activity: Activity[] }) {
  return <><PageHeader eyebrow="Audit trail" title="Project activity" description="Every plan mutation, generated revision, clone attachment, and GitHub reconciliation." />{activity.length ? <section className="activity-list">{activity.map((item) => <article key={item.activity_id}><span className="activity-icon"><ActivityIcon /></span><div><strong>{item.summary}</strong><p>{item.kind}</p></div><time>{timeAgo(item.created_at)}</time></article>)}</section> : <EmptyState icon={<ActivityIcon />} title="No activity recorded" description="DPT records meaningful project actions here." />}</>;
}

function SettingsPage({ dashboard, onChanged }: { dashboard: Dashboard; onChanged: () => Promise<void> }) {
  const [sourceBranch, setSourceBranch] = useState(dashboard.project.plan_source_branch);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function saveSourceBranch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      await api(`/api/projects/${dashboard.project.project_id}/plan/source-branch`, {
        method: "PUT",
        body: JSON.stringify({ plan_source_branch: sourceBranch.trim() }),
      });
      await onChanged();
      setSaved(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The plan source branch could not be saved");
    } finally {
      setSaving(false);
    }
  }

  return <>
    <PageHeader eyebrow="Project configuration" title="Settings" description="Review the project conventions DPT will enforce." />
    <section className="settings-grid">
      <article className="settings-card">
        <header><Github /><div><h2>GitHub labels</h2><p>Stable bindings between planned steps and delivery evidence.</p></div></header>
        <div className="label-examples"><code>dpt:project:{dashboard.project.slug}</code><code>dpt:release:{dashboard.project.release.toLowerCase()}</code><code>dpt:step:{"{step-id}"}</code></div>
      </article>
      <article className="settings-card">
        <header><ShieldCheck /><div><h2>Completion policy</h2><p>Human acceptance remains the trust boundary.</p></div></header>
        <div className="setting-row"><span>Merged pull request required</span><strong>Yes</strong></div>
        <div className="setting-row"><span>Closed unmerged PR</span><strong>Cancelled</strong></div>
      </article>
      <article className="settings-card settings-card-source-branch">
        <header><GitBranch /><div><h2>Plan source branch</h2><p>The common parent branch that distributes the plan.</p></div></header>
        <form onSubmit={saveSourceBranch}>
          {error && <ErrorBanner message={error} />}
          <label><span>Remote branch</span><div><GitBranch /><input onChange={(event) => { setSourceBranch(event.target.value); setSaved(false); }} required value={sourceBranch} /></div></label>
          <p>Reconcile fetches only <code>{dashboard.project.generated_filename}</code> from <code>origin/{sourceBranch || "…"}</code>. It never checks out or rebases your current feature branch.</p>
          <button className="button button-secondary button-small" disabled={saving || sourceBranch.trim() === dashboard.project.plan_source_branch}>
            {saving ? "Saving…" : saved ? "Saved" : "Save source branch"}
          </button>
        </form>
      </article>
      <article className="settings-card">
        <header><FileCode2 /><div><h2>Generated plan</h2><p>One identical implementation file in every clone.</p></div></header>
        <div className="setting-row"><span>Filename</span><code>{dashboard.project.generated_filename}</code></div>
        <div className="setting-row"><span>Revision</span><strong>{dashboard.project.revision}</strong></div>
      </article>
      <article className="settings-card">
        <header><CloudCog /><div><h2>Automation</h2><p>DPT prepares changes but does not bypass review.</p></div></header>
        <div className="setting-row"><span>Checked-out branch changes</span><strong>Never</strong></div>
        <div className="setting-row"><span>Plan import</span><strong>Newer + conflict-free</strong></div>
        <div className="setting-row"><span>Issue and PR mutation</span><strong>Approval required</strong></div>
      </article>
    </section>
  </>;
}

function AddRepositoryModal({ projectId, session, onClose, onCreated }: { projectId: string; session: Session | null; onClose: () => void; onCreated: () => void }) {
  const [repositories, setRepositories] = useState<GitHubRepository[]>([]);
  const [selected, setSelected] = useState<GitHubRepository | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (!session?.authenticated) return; setLoading(true); api<GitHubRepository[]>("/api/github/repositories").then(setRepositories).catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "GitHub repositories could not be loaded")).finally(() => setLoading(false)); }, [session]);
  async function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const data = new FormData(event.currentTarget); const fullName = selected?.full_name || String(data.get("github_full_name")); try { await api(`/api/projects/${projectId}/repositories`, { method: "POST", body: JSON.stringify({ alias: data.get("alias"), github_full_name: fullName, responsibility: data.get("responsibility"), github_default_branch: selected?.default_branch, clone_url: selected?.clone_url }) }); onCreated(); } catch (caught) { setError(caught instanceof Error ? caught.message : "Repository could not be added"); } }
  const filtered = repositories.filter((repo) => repo.full_name.toLowerCase().includes(query.toLowerCase())).slice(0, 8);
  return <Modal title="Add a managed repository" description="Select from GitHub or enter a repository manually. Local clone setup follows." onClose={onClose} wide><form className="form-stack" onSubmit={submit}>{error && <ErrorBanner message={error} />}{session?.authenticated ? <div className="github-picker"><SearchInput value={query} onChange={setQuery} placeholder="Search your GitHub repositories" />{loading ? <Loading label="Loading repositories" /> : <div className="github-repo-list">{filtered.map((repo) => <button className={selected?.id === repo.id ? "selected" : ""} key={repo.id} onClick={() => setSelected(repo)} type="button"><img src={repo.owner_avatar_url} alt="" /><span><strong>{repo.full_name}</strong><small>{repo.private ? "Private" : "Public"} · {repo.default_branch}</small></span>{selected?.id === repo.id && <CheckCircle2 />}</button>)}</div>}</div> : <label><span>GitHub repository</span><input name="github_full_name" placeholder="stateforge-org/stateforge-agent" required /></label>}<div className="form-grid"><label><span>DPT alias</span><input name="alias" placeholder="agent" required /></label><label><span>Responsibility</span><input name="responsibility" placeholder="Agent runtime and integration" /></label></div><div className="modal-actions"><button className="button button-ghost" onClick={onClose} type="button">Cancel</button><button className="button button-primary" disabled={session?.authenticated && !selected}><Plus />Add repository</button></div></form></Modal>;
}

function AddBlockerForm({
  dashboard,
  step,
  onCreated,
}: {
  dashboard: Dashboard;
  step: Step;
  onCreated: () => Promise<void>;
}) {
  const [mode, setMode] = useState<"existing" | "new">("existing");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const data = new FormData(event.currentTarget);
    const body = mode === "existing"
      ? { issue_url: String(data.get("issue_url") || "") }
      : {
          repository_id: String(data.get("repository_id") || ""),
          title: String(data.get("title") || ""),
          body: String(data.get("body") || ""),
        };
    try {
      await api<Blocker>(
        `/api/projects/${dashboard.project.project_id}/steps/${step.step_id}/blockers`,
        { method: "POST", body: JSON.stringify(body) },
      );
      await onCreated();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The blocker could not be added");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="add-blocker-form" onSubmit={submit}>
      <header className="blocker-composer-header">
        <span className="blocker-composer-icon"><Bug /></span>
        <div>
          <strong>{mode === "new" ? "Create a GitHub bug" : "Link a GitHub issue"}</strong>
          <p>
            {mode === "new"
              ? "Open a focused bug in a managed repository and block this milestone immediately."
              : "Use an existing issue as the GitHub-owned blocker for this milestone."}
          </p>
        </div>
        <div aria-label="Blocker source" className="segmented" role="group">
          <button className={mode === "existing" ? "active" : ""} onClick={() => setMode("existing")} type="button">Existing issue</button>
          <button className={mode === "new" ? "active" : ""} onClick={() => setMode("new")} type="button">Create bug</button>
        </div>
      </header>
      <div className="blocker-composer-body">
        {mode === "existing" ? (
          <div className="blocker-existing-grid">
            <label>
              <span>GitHub Issue URL</span>
              <div className="blocker-url-control">
                <Github />
                <input name="issue_url" placeholder="https://github.com/owner/repository/issues/11" required />
              </div>
              <small>The issue must belong to one of this project’s managed repositories.</small>
            </label>
            <aside>
              <Link2 />
              <div>
                <strong>Native GitHub dependency</strong>
                <p>DPT adds a blocked-by relationship. It does not copy the issue into the plan.</p>
              </div>
            </aside>
          </div>
        ) : (
          <div className="blocker-form-grid">
            <label className="blocker-repository-field">
              <span>Repository</span>
              <select defaultValue={step.repository_id} name="repository_id" required>
                {dashboard.repositories.map((repository) => (
                  <option key={repository.repository_id} value={repository.repository_id}>
                    {repository.github_full_name}
                  </option>
                ))}
              </select>
              <small>Where the fix will be implemented and reviewed.</small>
            </label>
            <label className="blocker-title-field">
              <span>Bug title</span>
              <div className="blocker-title-control">
                <code>[BUG][{dashboard.project.release.toUpperCase()}]</code>
                <input name="title" placeholder="Describe the concrete defect" required />
              </div>
              <small>Keep it specific—the generated prefix makes the release and issue type visible.</small>
            </label>
            <label className="blocker-description-field">
              <span>Description <small>Markdown supported</small></span>
              <textarea
                name="body"
                placeholder={"## Problem\nWhat is broken, and where?\n\n## Expected behavior\nWhat should happen instead?\n\n## Acceptance evidence\nHow will reviewers know it is fixed?"}
                rows={8}
              />
            </label>
          </div>
        )}
        {error && <ErrorBanner message={error} />}
      </div>
      <footer className="blocker-composer-footer">
        <div>
          <strong><CircleAlert />Blocks {step.step_id} immediately</strong>
          <span>Closing or unlinking the GitHub issue releases the milestone.</span>
        </div>
        <button className="button button-danger" disabled={saving}>
          <Bug />
          {saving ? "Saving blocker…" : mode === "existing" ? "Link blocker" : "Create bug and block milestone"}
        </button>
      </footer>
    </form>
  );
}

function StepDetail({ dashboard, session, step, onChanged, onClose, onOpenBlocker, onSelect }: { dashboard: Dashboard; session: Session | null; step: Step; onChanged: () => Promise<void>; onClose: () => void; onOpenBlocker: (blocker: Blocker) => void; onSelect: (step: Step) => void }) {
  const [addingBlocker, setAddingBlocker] = useState(false);
  const [creatingIssue, setCreatingIssue] = useState(false);
  const [issueError, setIssueError] = useState<string | null>(null);
  const [history, setHistory] = useState<Step[]>([]);
  const [pullRequestOpen, setPullRequestOpen] = useState(false);
  const [closing, setClosing] = useState(false);
  const [inspectingContext, setInspectingContext] = useState(false);
  const [approvingContext, setApprovingContext] = useState(false);
  const [packageLoading, setPackageLoading] = useState(false);
  const [verificationAction, setVerificationAction] = useState<string | null>(null);
  const [implementationPackage, setImplementationPackage] = useState<{ package_hash: string; markdown: string } | null>(null);
  const [contextError, setContextError] = useState<string | null>(null);
  const dependencies = step.depends_on
    .map((id) => dashboard.steps.find((candidate) => candidate.step_id === id))
    .filter((value): value is Step => Boolean(value));
  const downstream = dashboard.steps.filter((candidate) =>
    candidate.depends_on.includes(step.step_id),
  );
  const artifacts = [...step.artifacts].sort(
    (left, right) => Number(left.kind === "pull_request") - Number(right.kind === "pull_request"),
  );
  const issue = artifacts.find((artifact) => artifact.kind === "issue");
  const activePullRequest = artifacts.find(
    (artifact) =>
      artifact.kind === "pull_request" &&
      (artifact.state === "open" || artifact.is_merged),
  );
  const path = [...history, step];
  const assignees = stepAssignees(step);

  function requestClose() {
    if (closing) return;
    setClosing(true);
    window.setTimeout(onClose, 180);
  }

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") requestClose();
    }
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [closing, onClose]);

  useEffect(() => setPullRequestOpen(false), [step.step_id]);

  function selectMilestone(candidate: Step) {
    const existingIndex = path.findIndex(
      (milestone) => milestone.step_id === candidate.step_id,
    );
    setHistory(
      existingIndex >= 0 ? path.slice(0, existingIndex) : [...history, step],
    );
    onSelect(candidate);
  }

  function goBack() {
    const previous = history.at(-1);
    if (!previous) return;
    setHistory(history.slice(0, -1));
    onSelect(previous);
  }

  function selectPathMilestone(index: number) {
    if (index === path.length - 1) return;
    setHistory(path.slice(0, index));
    onSelect(path[index]);
  }

  async function createIssue() {
    setIssueError(null);
    setCreatingIssue(true);
    const githubTab = window.open("about:blank", "_blank");
    if (githubTab) githubTab.opener = null;
    try {
      const createdIssue = await api<{ number: number; html_url: string }>(
        `/api/projects/${dashboard.project.project_id}/steps/${step.step_id}/github/issues`,
        { method: "POST" },
      );
      if (githubTab) githubTab.location.href = createdIssue.html_url;
      else window.location.assign(createdIssue.html_url);
    } catch (caught) {
      githubTab?.close();
      setIssueError(
        caught instanceof Error
          ? caught.message
          : "The GitHub Issue could not be created",
      );
    } finally {
      setCreatingIssue(false);
    }
  }

  async function inspectContext() {
    setInspectingContext(true);
    setContextError(null);
    try {
      await api(`/api/projects/${dashboard.project.project_id}/steps/${step.step_id}/context/inspect`, { method: "POST" });
      await onChanged();
    } catch (caught) {
      setContextError(caught instanceof Error ? caught.message : "Context could not be inspected");
    } finally {
      setInspectingContext(false);
    }
  }

  async function approveContext() {
    setApprovingContext(true);
    setContextError(null);
    try {
      await api(`/api/projects/${dashboard.project.project_id}/steps/${step.step_id}/context/approve`, {
        method: "POST",
        body: JSON.stringify({ approved_by: session?.user?.login || "Local human" }),
      });
      await onChanged();
    } catch (caught) {
      setContextError(caught instanceof Error ? caught.message : "Context could not be approved");
    } finally {
      setApprovingContext(false);
    }
  }

  async function createImplementationPackage() {
    setPackageLoading(true);
    setContextError(null);
    try {
      const result = await api<{ package_hash: string; markdown: string }>(
        `/api/projects/${dashboard.project.project_id}/steps/${step.step_id}/implementation-package`,
        { method: "POST" },
      );
      setImplementationPackage(result);
    } catch (caught) {
      setContextError(caught instanceof Error ? caught.message : "Implementation package could not be created");
    } finally {
      setPackageLoading(false);
    }
  }

  async function recordVerification(
    verificationId: string,
    status: "passed" | "failed" | "skipped" | "unavailable",
  ) {
    const evidence = window.prompt(
      status === "passed"
        ? "What evidence did you inspect?"
        : "Record why this verification did not pass.",
      "",
    );
    if (evidence === null) return;
    setVerificationAction(verificationId);
    setContextError(null);
    try {
      await api(`/api/projects/${dashboard.project.project_id}/steps/${step.step_id}/verification/${verificationId}/record`, {
        method: "POST",
        body: JSON.stringify({
          status,
          recorded_by: session?.user?.login || "Local human",
          evidence,
        }),
      });
      await onChanged();
    } catch (caught) {
      setContextError(caught instanceof Error ? caught.message : "Verification evidence could not be recorded");
    } finally {
      setVerificationAction(null);
    }
  }

  async function runVerification(verificationId: string, target: string) {
    if (!window.confirm(`Run this command in its managed repository?\n\n${target}\n\nDPT runs the exact command without a shell and records its result.`)) return;
    setVerificationAction(verificationId);
    setContextError(null);
    try {
      await api(`/api/projects/${dashboard.project.project_id}/steps/${step.step_id}/verification/${verificationId}/run`, {
        method: "POST",
        body: JSON.stringify({
          approved_by: session?.user?.login || "Local human",
          approved_target: target,
        }),
      });
      await onChanged();
    } catch (caught) {
      setContextError(caught instanceof Error ? caught.message : "Verification command could not be run");
    } finally {
      setVerificationAction(null);
    }
  }

  return (
    <div
      className={
        closing
          ? "step-dialog-backdrop popup-closing"
          : "step-dialog-backdrop"
      }
      onMouseDown={requestClose}
    >
      <section
        aria-labelledby="step-dialog-title"
        aria-modal="true"
        className="step-dialog"
        onMouseDown={(event) => event.stopPropagation()}
        role="dialog"
      >
        <header>
          <div>
            {history.length > 0 && (
              <button
                aria-label="Go back to previous milestone"
                className="step-dialog-back"
                onClick={goBack}
              >
                <ArrowLeft />
              </button>
            )}
            <span className="eyebrow">Implementation step</span>
            <code>{step.step_id}</code>
          </div>
          <button
            aria-label="Close step details"
            className="icon-button"
            onClick={requestClose}
          >
            <X />
          </button>
        </header>
        <nav aria-label="Milestone path" className="step-path">
          <span>Path</span>
          {path.map((milestone, index) => (
            <span className="step-path-segment" key={`${milestone.step_id}-${index}`}>
              {index > 0 && <ChevronRight />}
              <button
                aria-current={index === path.length - 1 ? "page" : undefined}
                onClick={() => selectPathMilestone(index)}
                type="button"
              >
                {milestone.step_id}
              </button>
            </span>
          ))}
        </nav>
        <div className="step-dialog-title">
          <div className="step-dialog-title-row">
            <div className="step-dialog-title-copy">
              <div className="step-dialog-status-line">
                <span>{step.repository_alias}</span>
                <StatusBadge status={step.computed_state} />
              </div>
              <h2 id="step-dialog-title">{step.title}</h2>
              <div className="step-dialog-assignees">
                <AssigneeList accounts={assignees} />
              </div>
            </div>
            <div className="step-dialog-github-actions">
              {issue ? (
                <a
                  className="button button-secondary"
                  href={issue.html_url}
                  rel="noreferrer"
                  target="_blank"
                >
                  <CircleDot />
                  Open Issue
                  <ExternalLink />
                </a>
              ) : (
                <button
                  className="button button-primary"
                  disabled={
                    creatingIssue ||
                    !session?.authenticated ||
                    !step.dependencies_complete
                  }
                  onClick={createIssue}
                  title={
                    step.dependencies_complete
                      ? undefined
                      : "Complete this milestone's dependencies first"
                  }
                >
                  <CircleDot />
                  {creatingIssue ? "Creating Issue…" : "Create Issue"}
                </button>
              )}
              {activePullRequest ? (
                <a
                  className="button button-primary"
                  href={activePullRequest.html_url}
                  rel="noreferrer"
                  target="_blank"
                >
                  <GitPullRequest />
                  Open PR
                  <ExternalLink />
                </a>
              ) : issue ? (
                <button
                  className="button button-primary"
                  disabled={!session?.authenticated || !step.dependencies_complete || !step.blockers_clear}
                  onClick={() => setPullRequestOpen((open) => !open)}
                  title={
                    !step.dependencies_complete
                      ? "Complete this milestone's dependencies first"
                      : !step.blockers_clear
                        ? "Resolve this milestone's GitHub blockers first"
                        : undefined
                  }
                >
                  <GitPullRequest />
                  {pullRequestOpen ? "Cancel PR" : "Open PR"}
                </button>
              ) : null}
            </div>
          </div>
          <div className="github-markdown milestone-markdown">
            <Markdown>{step.required_result}</Markdown>
          </div>
          {issueError && <p className="step-dialog-action-error">{issueError}</p>}
        </div>
        <section className={`context-readiness context-readiness-${step.context_readiness.state}`}>
          <div className="context-readiness-icon"><ShieldCheck /></div>
          <div>
            <span>Implementation readiness</span>
            <strong>{step.context_readiness.ready ? "Context approved — ready to implement" : step.context_readiness.state.replaceAll("_", " ")}</strong>
            {step.context_readiness.reasons.length ? (
              <ul>{step.context_readiness.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
            ) : <p>The milestone boundary, protected behavior, and proof are explicit.</p>}
            <div className={`risk-pressure risk-pressure-${step.risk_assessment.level}`} title={step.risk_assessment.explanation}>
              <span>Review pressure: {step.risk_assessment.level}</span>
              {step.risk_assessment.factors.map((factor) => <small key={factor.kind}>{factor.explanation}</small>)}
            </div>
          </div>
          <div className="context-readiness-actions">
            <button className="button button-secondary button-small" disabled={inspectingContext} onClick={inspectContext} type="button"><RefreshCw />{inspectingContext ? "Inspecting…" : "Inspect context"}</button>
            <button className="button button-secondary button-small" disabled={approvingContext || !step.context_resources.length} onClick={approveContext} type="button"><CheckCircle2 />{approvingContext ? "Approving…" : "Approve snapshot"}</button>
            <button className="button button-primary button-small" disabled={!step.context_readiness.ready || packageLoading} onClick={createImplementationPackage} type="button"><Sparkles />{packageLoading ? "Building package…" : "Start implementation"}</button>
          </div>
        </section>
        {contextError && <p className="step-dialog-action-error context-action-error">{contextError}</p>}
        {pullRequestOpen && (
          <div className="github-action-panel">
            <PullRequestBuilder dashboard={dashboard} step={step} />
          </div>
        )}
        <div className="detail-section detail-section-github">
          <span>GitHub delivery</span>
          {artifacts.length ? (
            <div className="github-records">
              {artifacts.map((artifact) => (
                <GitHubRecord
                  artifact={artifact}
                  key={artifact.artifact_id}
                  repository={step.github_full_name}
                />
              ))}
            </div>
          ) : (
            <div className="no-evidence">
              <Github />
              <div>
                <strong>No labelled Issue or pull request found</strong>
                <p>
                  DPT expects <code>dpt:step:{step.step_id}</code> on the matching
                  GitHub artifact.
                </p>
                {step.computed_state === "blocked" ? (
                  <p className="no-evidence-blocked">
                    Complete the dependencies below before opening this work.
                  </p>
                ) : (
                  <p>Use the GitHub action at the top to create the Issue.</p>
                )}
              </div>
            </div>
          )}
        </div>
        <div className="detail-section">
          <span>Planned branch</span>
          <div className="planned-branch">
            <a
              className="branch-code"
              href={githubBranchUrl(step.github_full_name, step.branch_name)}
              rel="noreferrer"
              target="_blank"
            >
              <GitBranch />
              {step.branch_name}
              <ExternalLink />
            </a>
          </div>
        </div>
        <div className="detail-section milestone-context-section">
          <span>Context contract</span>
          {step.context_resources.length ? (
            <div className="milestone-context-list">
              {step.context_resources.map((resource) => (
                <article className={`milestone-context-item context-role-border-${resource.role}`} key={resource.context_id}>
                  <header><span><code>{resource.repository_alias}/{resource.path}</code><small>{resource.selector_kind.replaceAll("_", " ")}</small></span><strong>{resource.role.replaceAll("_", " ")}</strong></header>
                  <p>{resource.explanation}</p>
                  <footer><span>{resource.authority.replaceAll("_", " ")}</span><span>{resource.required ? "Required" : "Optional"}</span><span className={`context-observation context-observation-${resource.observation?.status || "uninspected"}`}>{resource.observation?.status.replaceAll("_", " ") || "not yet inspected"}</span></footer>
                </article>
              ))}
            </div>
          ) : <p className="muted-copy">No implementation context has been declared. Add it in the Plan editor.</p>}
        </div>
        <div className="detail-section milestone-verification-section">
          <span>Verification contract</span>
          {step.verification_requirements.length ? (
            <div className="milestone-verification-list">
              {step.verification_requirements.map((requirement) => (
                <article className={requirement.observation ? `verification-${requirement.observation.status}` : "verification-not-recorded"} key={requirement.verification_id}>
                  <ListChecks />
                  <span>
                    <strong>{requirement.kind.replaceAll("_", " ")} · {requirement.target}</strong>
                    <small>{requirement.explanation}</small>
                    {requirement.observation && (
                      <span className="verification-observation">
                        <b>{requirement.observation.stale ? `${requirement.observation.status} · stale` : requirement.observation.status}</b>
                        <span>{requirement.observation.recorded_by} · {new Date(requirement.observation.observed_at).toLocaleString()}</span>
                        {requirement.observation.evidence && <small>{requirement.observation.evidence}</small>}
                        {requirement.observation.output_excerpt && <details><summary>Command output</summary><pre>{requirement.observation.output_excerpt}</pre></details>}
                      </span>
                    )}
                  </span>
                  <div className="verification-actions">
                    {requirement.kind !== "human" && requirement.repository_id && (
                      <button className="button button-secondary button-small" disabled={verificationAction === requirement.verification_id} onClick={() => runVerification(requirement.verification_id, requirement.target)} type="button"><Play />Run</button>
                    )}
                    <button className="button button-secondary button-small" disabled={verificationAction === requirement.verification_id} onClick={() => recordVerification(requirement.verification_id, "passed")} type="button"><CheckCircle2 />Pass</button>
                    <button className="button button-secondary button-small" disabled={verificationAction === requirement.verification_id} onClick={() => recordVerification(requirement.verification_id, "failed")} type="button"><X />Fail</button>
                  </div>
                  {requirement.required && <em>Required</em>}
                </article>
              ))}
            </div>
          ) : <p className="muted-copy">No proof has been defined. The milestone cannot become context-ready.</p>}
        </div>
        {step.impact_analysis && (
          <div className="detail-section milestone-impact-section">
            <span>Pull request change surface</span>
            <div className="impact-summary-grid">
              <div className="impact-expected"><strong>{step.impact_analysis.expected_changes.length}</strong><span>Expected files changed</span></div>
              <div className={step.impact_analysis.unexpected_changes.length ? "impact-unexpected active" : "impact-unexpected"}><strong>{step.impact_analysis.unexpected_changes.length}</strong><span>Unexpected files</span></div>
              <div className={step.impact_analysis.untouched_expected.length ? "impact-untouched active" : "impact-untouched"}><strong>{step.impact_analysis.untouched_expected.length}</strong><span>Expected areas untouched</span></div>
              <div className={step.impact_analysis.preserved_resources_changed.length ? "impact-preserved active" : "impact-preserved"}><strong>{step.impact_analysis.preserved_resources_changed.length}</strong><span>Preserved files changed</span></div>
              <div><strong>{step.impact_analysis.verification_resources_changed.length}</strong><span>Verification files changed</span></div>
              <div><strong>{step.impact_analysis.checks_state || "—"}</strong><span>GitHub checks</span></div>
            </div>
            {step.impact_analysis.unexpected_changes.length > 0 && <div className="impact-path-list"><strong>Outside the declared change surface — review these files on GitHub</strong>{step.impact_analysis.unexpected_changes.map((path) => <code key={path}>{path}</code>)}</div>}
            {step.impact_analysis.preserved_resources_changed.length > 0 && <div className="impact-path-list impact-preserved-list"><strong>Protected context changed — verify these files carefully</strong>{step.impact_analysis.preserved_resources_changed.map((path) => <code key={path}>{path}</code>)}</div>}
          </div>
        )}
        {step.regression_history.length > 0 && (
          <div className="detail-section regression-memory-section">
            <span>Regression memory</span>
            <p className="regression-memory-intro">Resolved GitHub blockers remain attached to future implementation packages so the same context gap is not rediscovered from scratch.</p>
            <div className="regression-memory-list">
              {step.regression_history.map((regression) => (
                <article key={regression.blocker_id}>
                  <header><Bug /><span><a href={regression.url} rel="noreferrer" target="_blank">{regression.issue}</a><strong>{regression.title}</strong></span><em>{regression.status}</em></header>
                  {regression.matching_context_paths.length > 0 && <div><small>Previous corrective work touches this milestone’s context</small>{regression.matching_context_paths.map((path) => <code key={path}>{path}</code>)}</div>}
                  {regression.context_gaps.length > 0 && <div><small>Corrective files outside the current context contract</small>{regression.context_gaps.map((path) => <code key={path}>{path}</code>)}</div>}
                  {regression.implementation_package && <footer>Original package <code>{regression.implementation_package.package_hash.slice(0, 12)}</code></footer>}
                </article>
              ))}
            </div>
          </div>
        )}
        <div className="detail-section blocker-section">
          <div className="detail-section-heading">
            <span>GitHub blockers</span>
            <button
              className="button button-secondary button-small"
              disabled={!session?.authenticated || !issue}
              onClick={() => setAddingBlocker((open) => !open)}
              title={issue ? undefined : "Create the milestone Issue first"}
              type="button"
            >
              <Link2 />
              {addingBlocker ? "Cancel" : "Add blocker"}
            </button>
          </div>
          {addingBlocker && (
            <AddBlockerForm
              dashboard={dashboard}
              step={step}
              onCreated={async () => {
                setAddingBlocker(false);
                await onChanged();
              }}
            />
          )}
          {step.blockers.length ? (
            <div className="blocker-link-list">
              {step.blockers.map((blocker) => (
                <button
                  className={`blocker-link-card blocker-${blocker.status}`}
                  key={blocker.blocker_id}
                  onClick={() => onOpenBlocker(blocker)}
                  type="button"
                >
                  <span className="blocker-link-icon"><Bug /></span>
                  <span>
                    <small>{blocker.repository_alias} #{blocker.number}</small>
                    <strong>{blocker.title}</strong>
                  </span>
                  <span className={`blocker-status blocker-status-${blocker.status}`}>
                    {blocker.status}
                  </span>
                  <ChevronRight />
                </button>
              ))}
            </div>
          ) : (
            <p className="muted-copy">
              No GitHub issue currently blocks this milestone.
            </p>
          )}
        </div>
        <div className="detail-section">
          <span>Dependencies</span>
          {dependencies.length ? (
            <div className="related-steps">
              {dependencies.map((dependency) => (
                <button
                  key={dependency.step_id}
                  onClick={() => selectMilestone(dependency)}
                >
                  <code>{dependency.step_id}</code>
                  <span>{dependency.title}</span>
                  <StatusBadge status={dependency.computed_state} />
                </button>
              ))}
            </div>
          ) : (
            <p className="muted-copy">Foundation step with no prerequisites.</p>
          )}
        </div>
        <div className="detail-section">
          <span>Unlocks</span>
          {downstream.length ? (
            <div className="related-steps">
              {downstream.map((candidate) => (
                <button
                  key={candidate.step_id}
                  onClick={() => selectMilestone(candidate)}
                >
                  <code>{candidate.step_id}</code>
                  <span>{candidate.title}</span>
                  <ChevronRight />
                </button>
              ))}
            </div>
          ) : (
            <p className="muted-copy">No downstream step declared.</p>
          )}
        </div>
      </section>
      {implementationPackage && (
        <div className="implementation-package-backdrop" onMouseDown={() => setImplementationPackage(null)}>
          <section onMouseDown={(event) => event.stopPropagation()}>
            <header><Sparkles /><div><strong>Agent-ready implementation package</strong><small>Deterministic package <code>{implementationPackage.package_hash.slice(0, 12)}</code> records exactly what the agent received.</small></div><button onClick={() => setImplementationPackage(null)} type="button"><X /></button></header>
            <div className="implementation-package-actions"><button className="button button-primary" onClick={() => navigator.clipboard.writeText(implementationPackage.markdown)} type="button"><FileCode2 />Copy implementation instructions</button></div>
            <pre>{implementationPackage.markdown}</pre>
          </section>
        </div>
      )}
    </div>
  );
}

function BlockerDetail({
  blocker,
  dashboard,
  session,
  onChanged,
  onClose,
  onOpenStep,
}: {
  blocker: Blocker;
  dashboard: Dashboard;
  session: Session | null;
  onChanged: () => Promise<void>;
  onClose: () => void;
  onOpenStep: (step: Step) => void;
}) {
  const [closing, setClosing] = useState(false);
  const [pullRequestOpen, setPullRequestOpen] = useState(false);
  const [unlinking, setUnlinking] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const issue = blocker.artifacts.find(
    (artifact) => artifact.artifact_id === blocker.blocker_id,
  ) || blocker;
  const activePullRequest = blocker.artifacts.find(
    (artifact) => artifact.kind === "pull_request" && (artifact.state === "open" || artifact.is_merged),
  );
  const blockedSteps = blocker.blocked_step_ids
    .map((stepId) => dashboard.steps.find((step) => step.step_id === stepId))
    .filter((step): step is Step => Boolean(step));

  function requestClose() {
    if (closing) return;
    setClosing(true);
    window.setTimeout(onClose, 180);
  }

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") requestClose();
    }
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [closing, onClose]);

  async function unlink(step: Step) {
    setError(null);
    setUnlinking(step.step_id);
    try {
      await api<void>(
        `/api/projects/${dashboard.project.project_id}/steps/${step.step_id}/blockers/${encodeURIComponent(blocker.blocker_id)}`,
        { method: "DELETE" },
      );
      await onChanged();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The blocker could not be unlinked");
    } finally {
      setUnlinking(null);
    }
  }

  return (
    <div className={closing ? "step-dialog-backdrop popup-closing" : "step-dialog-backdrop"} onMouseDown={requestClose}>
      <section aria-labelledby="blocker-dialog-title" aria-modal="true" className="step-dialog blocker-dialog" onMouseDown={(event) => event.stopPropagation()} role="dialog">
        <header>
          <div><span className="eyebrow"><Bug />GitHub blocker</span><code>{blocker.repository_alias} #{blocker.number}</code></div>
          <button aria-label="Close blocker details" className="icon-button" onClick={requestClose}><X /></button>
        </header>
        <nav aria-label="Blocker source" className="step-path blocker-path">
          <span>Source</span><Github /><span>{blocker.github_full_name}</span><ChevronRight /><strong>Issue #{blocker.number}</strong>
        </nav>
        <div className="step-dialog-title blocker-dialog-title">
          <div className="step-dialog-title-row">
            <div className="step-dialog-title-copy">
              <div className="step-dialog-status-line"><span>GitHub issue dependency</span><span className={`blocker-status blocker-status-${blocker.status}`}>{blocker.status}</span></div>
              <h2 id="blocker-dialog-title">{blocker.title}</h2>
              <div className="step-dialog-assignees"><AssigneeList accounts={blocker.assignees} /></div>
            </div>
            <div className="step-dialog-github-actions">
              <a className="button button-secondary" href={blocker.html_url} rel="noreferrer" target="_blank"><CircleDot />Open Issue<ExternalLink /></a>
              {activePullRequest ? (
                <a className="button button-primary blocker-primary-action" href={activePullRequest.html_url} rel="noreferrer" target="_blank"><GitPullRequest />Open PR<ExternalLink /></a>
              ) : blocker.is_active ? (
                <button className="button button-primary blocker-primary-action" disabled={!session?.authenticated} onClick={() => setPullRequestOpen((open) => !open)}><GitPullRequest />{pullRequestOpen ? "Cancel PR" : "Open PR"}</button>
              ) : null}
            </div>
          </div>
          {error && <p className="step-dialog-action-error">{error}</p>}
        </div>
        {pullRequestOpen && (
          <div className="github-action-panel blocker-action-panel">
            <PullRequestBuilder blocker={blocker} dashboard={dashboard} />
          </div>
        )}
        <div className="detail-section detail-section-github blocker-github-delivery">
          <span>GitHub delivery</span>
          <div className="github-records">
            {blocker.artifacts.map((artifact) => (
              <GitHubRecord artifact={artifact} key={artifact.artifact_id} repository={blocker.github_full_name} />
            ))}
          </div>
        </div>
        <div className="detail-section">
          <span>Suggested fix branch</span>
          <div className="planned-branch">
            <a className="branch-code blocker-branch" href={githubBranchUrl(blocker.github_full_name, blocker.suggested_branch)} rel="noreferrer" target="_blank"><GitBranch />{blocker.suggested_branch}<ExternalLink /></a>
          </div>
        </div>
        <div className="detail-section blocker-links-section">
          <span>Blocking milestones</span>
          <div className="related-steps">
            {blockedSteps.map((step) => (
              <div className="blocked-milestone-row" key={step.step_id}>
                <button onClick={() => onOpenStep(step)} type="button"><code>{step.step_id}</code><span>{step.title}</span><StatusBadge status={step.computed_state} /></button>
                <button aria-label={`Unlink ${blocker.title} from ${step.step_id}`} className="unlink-blocker" disabled={unlinking === step.step_id || !session?.authenticated} onClick={() => unlink(step)} title="Remove the GitHub blocked-by relationship" type="button"><Unlink />{unlinking === step.step_id ? "Unlinking…" : "Unlink"}</button>
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}

function GitHubRecord({
  artifact,
  repository,
}: {
  artifact: Artifact;
  repository: string;
}) {
  const isPullRequest = artifact.kind === "pull_request";
  const author = artifact.author_login
    ? [...artifact.assignees, ...artifact.reviewers].find(
        (account) => account.login === artifact.author_login,
      ) || {
        login: artifact.author_login,
        avatar_url: `https://github.com/${encodeURIComponent(artifact.author_login)}.png?size=64`,
        html_url: `https://github.com/${encodeURIComponent(artifact.author_login)}`,
      }
    : null;
  const state = artifact.is_merged
    ? "Merged"
    : artifact.is_draft
      ? "Draft"
      : artifact.state === "open"
        ? "Open"
        : "Closed";
  const stateClass = artifact.is_merged
    ? "merged"
    : artifact.state === "open"
      ? "open"
      : "closed";

  return (
    <article className="github-record">
      <header>
        <span className="github-record-icon">
          {isPullRequest ? <GitPullRequest /> : <CircleDot />}
        </span>
        <div className="github-record-heading">
          <div>
            <span>{isPullRequest ? "Pull request" : "Issue"}</span>
            <strong>#{artifact.number}</strong>
            <span className={`github-record-state ${stateClass}`}>{state}</span>
          </div>
          <h3>{artifact.title}</h3>
        </div>
        <div className="github-record-actions">
          {isPullRequest && (
            <a
              className="button button-primary github-record-review"
              href={`${artifact.html_url.replace(/\/$/, "")}/files`}
              rel="noreferrer"
              target="_blank"
            >
              <FileDiff />
              {artifact.state === "open" && !artifact.is_merged
                ? "Review changes"
                : "View changes"}
              <ExternalLink />
            </a>
          )}
          <a
            className="button button-secondary github-record-open"
            href={artifact.html_url}
            rel="noreferrer"
            target="_blank"
          >
            <Github />
            Open on GitHub
            <ExternalLink />
          </a>
        </div>
      </header>
      <div className="github-record-metadata">
        {author ? (
          <a
            className="github-record-author"
            href={author.html_url || undefined}
            rel="noreferrer"
            target="_blank"
          >
            {author.avatar_url ? (
              <img src={author.avatar_url} alt="" />
            ) : (
              <span>{author.login.slice(0, 1).toUpperCase()}</span>
            )}
            <span>Author <strong>{author.login}</strong></span>
          </a>
        ) : (
          <span><Users />Author <strong>Unknown</strong></span>
        )}
        <span>Updated <strong>{timeAgo(artifact.updated_at)}</strong></span>
        {isPullRequest && artifact.head_branch && artifact.base_branch && (
          <span className="github-record-branches">
            <GitMerge />
            <a
              href={githubBranchUrl(repository, artifact.head_branch)}
              rel="noreferrer"
              target="_blank"
            >
              <code>{artifact.head_branch}</code>
            </a>
            <ArrowRight />
            <a
              href={githubBranchUrl(repository, artifact.base_branch)}
              rel="noreferrer"
              target="_blank"
            >
              <code>{artifact.base_branch}</code>
            </a>
          </span>
        )}
        {artifact.assignees.length > 0 && (
          <AssigneeList accounts={artifact.assignees} />
        )}
        {artifact.reviewers.length > 0 && (
          <span>
            Reviewers{" "}
            <strong>
              {artifact.reviewers.map((reviewer) => reviewer.login).join(", ")}
            </strong>
          </span>
        )}
        {isPullRequest && artifact.checks_state && (
          <span>Checks <strong>{artifact.checks_state}</strong></span>
        )}
      </div>
      {artifact.labels.length > 0 && (
        <div className="github-record-labels">
          {artifact.labels.map((label) => <code key={label}>{label}</code>)}
        </div>
      )}
      <div className={artifact.body ? "github-markdown" : "github-markdown empty"}>
        {artifact.body ? (
          <ReactMarkdown
            components={{
              a: ({ children, href }) => (
                <a href={href} rel="noreferrer" target="_blank">
                  {children}
                </a>
              ),
            }}
            remarkPlugins={[remarkGfm]}
          >
            {artifact.body}
          </ReactMarkdown>
        ) : (
          <p>No description was provided.</p>
        )}
      </div>
      {artifact.timeline.length > 0 && (
        <GitHubTimeline items={artifact.timeline} repository={repository} />
      )}
    </article>
  );
}

function timelineAction(item: GitHubTimelineItem) {
  switch (item.event) {
    case "commented":
      return "commented";
    case "review_comment":
      return item.path
        ? `commented on ${item.path}${item.line ? `:${item.line}` : ""}`
        : "left an inline review comment";
    case "reviewed":
      if (item.state === "approved") return "approved these changes";
      if (item.state === "changes_requested") return "requested changes";
      if (item.state === "dismissed") return "had a review dismissed";
      return "reviewed these changes";
    case "committed":
      return "added a commit";
    case "labeled":
      return "added a label";
    case "unlabeled":
      return "removed a label";
    case "assigned":
      return `assigned ${item.assignee || "a contributor"}`;
    case "unassigned":
      return `unassigned ${item.assignee || "a contributor"}`;
    case "review_requested":
      return `requested a review from ${item.requested_reviewer || "a reviewer"}`;
    case "review_request_removed":
      return `removed the review request for ${item.requested_reviewer || "a reviewer"}`;
    case "renamed":
      return `renamed the title from “${item.rename_from || ""}” to “${item.rename_to || ""}”`;
    case "merged":
      return "merged this pull request";
    case "closed":
      return "closed this item";
    case "reopened":
      return "reopened this item";
    case "head_ref_deleted":
      return "deleted the source branch";
    case "head_ref_restored":
      return "restored the source branch";
    default:
      return item.event.replaceAll("_", " ");
  }
}

function timelineIcon(event: string) {
  if (event === "committed") return <GitCommitHorizontal />;
  if (event === "labeled" || event === "unlabeled") return <Tag />;
  if (event === "assigned" || event === "unassigned") return <UserPlus />;
  if (event === "merged") return <GitMerge />;
  if (event.includes("review")) return <FileDiff />;
  if (event === "commented") return <MessageSquare />;
  return <CircleDot />;
}

function GitHubTimeline({
  items,
  repository,
}: {
  items: GitHubTimelineItem[];
  repository: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const timelineId = useId();

  return (
    <section className={expanded ? "github-timeline expanded" : "github-timeline"}>
      <button
        aria-controls={timelineId}
        aria-expanded={expanded}
        className="github-timeline-toggle"
        onClick={() => setExpanded((current) => !current)}
        type="button"
      >
        <span><ActivityIcon />Conversation and activity</span>
        <small>
          {items.length} {items.length === 1 ? "event" : "events"}
          <ChevronDown className="github-timeline-chevron" />
        </small>
      </button>
      <div
        aria-hidden={!expanded}
        className="github-timeline-content"
        id={timelineId}
        inert={!expanded}
      >
        <div className="github-timeline-items">
        {items.map((item) => {
          const isConversation = [
            "commented",
            "review_comment",
            "reviewed",
          ].includes(item.event);
          const actorUrl = item.actor?.html_url || (
            item.actor
              ? `https://github.com/${encodeURIComponent(item.actor.login)}`
              : null
          );
          const itemUrl = item.html_url || (
            item.commit_sha
              ? `https://github.com/${repository}/commit/${item.commit_sha}`
              : null
          );

          return (
            <div
              className={isConversation ? "github-timeline-conversation" : "github-timeline-event"}
              key={item.timeline_id}
            >
              {actorUrl ? (
                <a
                  className="github-timeline-avatar"
                  href={actorUrl}
                  rel="noreferrer"
                  target="_blank"
                >
                  {item.actor?.avatar_url ? (
                    <img src={item.actor.avatar_url} alt="" />
                  ) : (
                    <span>{item.actor?.login.slice(0, 1).toUpperCase()}</span>
                  )}
                </a>
              ) : (
                <span className="github-timeline-avatar github-timeline-avatar-event">
                  {timelineIcon(item.event)}
                </span>
              )}
              {isConversation ? (
                <article className="github-comment">
                  <header>
                    <span>
                      <strong>{item.actor?.login || "GitHub"}</strong>{" "}
                      {timelineAction(item)}
                    </span>
                    <span className="github-comment-meta">
                      {item.state && (
                        <strong className={`review-state review-state-${item.state}`}>
                          {item.state.replaceAll("_", " ")}
                        </strong>
                      )}
                      {item.created_at && <time>{timeAgo(item.created_at)}</time>}
                      {itemUrl && (
                        <a href={itemUrl} rel="noreferrer" target="_blank" title="Open this event on GitHub">
                          <ExternalLink />
                        </a>
                      )}
                    </span>
                  </header>
                  {item.diff_hunk && (
                    <pre className="github-review-diff"><code>{item.diff_hunk}</code></pre>
                  )}
                  <div className={item.body ? "github-markdown" : "github-markdown empty"}>
                    {item.body ? (
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {item.body}
                      </ReactMarkdown>
                    ) : (
                      <p>No written review summary.</p>
                    )}
                  </div>
                </article>
              ) : (
                <div className="github-event-copy">
                  <span>
                    <strong>{item.actor?.login || "GitHub"}</strong>{" "}
                    {timelineAction(item)}
                    {item.label && (
                      <code className="github-event-label">{item.label}</code>
                    )}
                  </span>
                  {item.commit_sha && (
                    <a
                      className="github-event-commit"
                      href={itemUrl || undefined}
                      rel="noreferrer"
                      target="_blank"
                    >
                      <code>{item.commit_sha.slice(0, 7)}</code>
                      {item.commit_message && <span>{item.commit_message.split("\n", 1)[0]}</span>}
                    </a>
                  )}
                  {item.created_at && <time>{timeAgo(item.created_at)}</time>}
                </div>
              )}
            </div>
          );
        })}
        </div>
      </div>
    </section>
  );
}

function PullRequestBuilder({ dashboard, step, blocker }: { dashboard: Dashboard; step?: Step; blocker?: Blocker }) {
  const plannedBranch = blocker?.suggested_branch || step?.branch_name || "";
  const targetKey = blocker?.blocker_id || step?.step_id || "";
  const targetApi = blocker
    ? `/api/projects/${dashboard.project.project_id}/blockers/${encodeURIComponent(blocker.blocker_id)}/github`
    : `/api/projects/${dashboard.project.project_id}/steps/${step?.step_id}/github`;
  const targetLabel = blocker ? "Blocker fix" : "Milestone";
  const [branches, setBranches] = useState<string[]>([]);
  const [baseBranch, setBaseBranch] = useState("");
  const [headBranch, setHeadBranch] = useState(plannedBranch);
  const [comparison, setComparison] = useState<{
    status: string;
    ahead_by: number;
    behind_by: number;
    total_commits: number;
  } | null>(null);
  const [comparisonLoading, setComparisonLoading] = useState(false);
  const [comparisonError, setComparisonError] = useState<string | null>(null);
  const [comparisonRevision, setComparisonRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [creatingBranch, setCreatingBranch] = useState(false);
  const [creatingPullRequest, setCreatingPullRequest] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const headBranchExists = branches.includes(headBranch);
  const plannedBranchExists = branches.includes(plannedBranch);
  const branchOptions = headBranchExists ? branches : [headBranch, ...branches];

  useEffect(() => {
    api<{ branches: string[]; planned_branch: string; default_base: string }>(
      `${targetApi}/branches`,
    ).then((result) => {
      setBranches(result.branches);
      setBaseBranch(result.default_base);
      setHeadBranch(result.planned_branch);
    }).catch((caught: unknown) => {
      setError(
        caught instanceof Error
          ? caught.message
          : "GitHub branches could not be loaded",
      );
    }).finally(() => setLoading(false));
  }, [dashboard.project.project_id, plannedBranch, targetApi, targetKey]);

  useEffect(() => {
    if (
      !baseBranch ||
      !headBranch ||
      baseBranch === headBranch ||
      !headBranchExists
    ) {
      setComparison(null);
      setComparisonError(null);
      setComparisonLoading(false);
      return;
    }

    let cancelled = false;
    const query = new URLSearchParams({
      base_branch: baseBranch,
      head_branch: headBranch,
    });
    setComparison(null);
    setComparisonError(null);
    setComparisonLoading(true);
    api<{
      status: string;
      ahead_by: number;
      behind_by: number;
      total_commits: number;
    }>(
      `${targetApi}/branch-comparison?${query}`,
    ).then((result) => {
      if (!cancelled) setComparison(result);
    }).catch((caught: unknown) => {
      if (cancelled) return;
      setComparisonError(
        caught instanceof Error
          ? caught.message
          : "The selected branches could not be compared",
      );
    }).finally(() => {
      if (!cancelled) setComparisonLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [
    baseBranch,
    dashboard.project.project_id,
    headBranch,
    headBranchExists,
    comparisonRevision,
    targetApi,
    targetKey,
  ]);

  async function createPlannedBranch() {
    setError(null);
    setMessage(null);
    setCreatingBranch(true);
    try {
      const branch = await api<{ name: string; base_branch: string }>(
        `${targetApi}/branches`,
        {
          method: "POST",
          body: JSON.stringify({ base_branch: baseBranch }),
        },
      );
      setBranches((current) => [...new Set([...current, branch.name])]);
      setHeadBranch(branch.name);
      setMessage(
        `Created ${branch.name} from ${branch.base_branch}. Push implementation commits before opening the pull request.`,
      );
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "The planned branch could not be created",
      );
    } finally {
      setCreatingBranch(false);
    }
  }

  async function createPullRequest() {
    setError(null);
    setMessage(null);
    setCreatingPullRequest(true);
    const githubTab = window.open("about:blank", "_blank");
    if (githubTab) githubTab.opener = null;
    try {
      const pullRequest = await api<{ number: number; html_url: string }>(
        `${targetApi}/pull-requests`,
        {
          method: "POST",
          body: JSON.stringify({
            base_branch: baseBranch,
            head_branch: headBranch,
          }),
        },
      );
      if (githubTab) githubTab.location.href = pullRequest.html_url;
      else window.location.assign(pullRequest.html_url);
    } catch (caught) {
      githubTab?.close();
      setError(
        caught instanceof Error
          ? caught.message
          : "The pull request could not be created",
      );
    } finally {
      setCreatingPullRequest(false);
    }
  }

  return (
    <div className="pull-request-builder">
      <header>
        <div>
          <strong>{targetLabel} pull request</strong>
          <p>
            Review changes from the release-scoped branch before merging them into
            the project branch.
          </p>
        </div>
        <Github />
      </header>
      {loading ? (
        <Loading label="Loading GitHub branches" />
      ) : (
        <>
          <div className="branch-flow" aria-label="Pull request merge direction">
            <div>
              <span>Changes from</span>
              <code>{headBranch}</code>
            </div>
            <ArrowRight />
            <div>
              <span>Merge into</span>
              <code>{baseBranch}</code>
            </div>
          </div>
          <details className="branch-advanced">
            <summary>Advanced branch selection</summary>
            <div className="branch-selection">
              <label>
                <span>Changes from</span>
                <select
                  value={headBranch}
                  onChange={(event) => setHeadBranch(event.target.value)}
                >
                  {branchOptions.map((branch) => (
                    <option key={branch} value={branch}>
                      {branch}{!branches.includes(branch) ? " (not created)" : ""}
                    </option>
                  ))}
                </select>
              </label>
              <ArrowRight />
              <label>
                <span>Merge into</span>
                <select
                  value={baseBranch}
                  onChange={(event) => setBaseBranch(event.target.value)}
                >
                  {branches.map((branch) => <option key={branch}>{branch}</option>)}
                </select>
              </label>
            </div>
          </details>
          {!plannedBranchExists && headBranch === plannedBranch && (
            <div className="branch-create-callout">
              <div>
                <strong>The suggested branch does not exist on GitHub.</strong>
                <p>DPT will create it from <code>{baseBranch}</code>.</p>
              </div>
              <button
                className="button button-secondary button-small"
                disabled={creatingBranch || !baseBranch}
                onClick={createPlannedBranch}
              >
                <GitBranch />
                {creatingBranch ? "Creating branch…" : "Create suggested branch"}
              </button>
            </div>
          )}
          {baseBranch === headBranch && (
            <p className="branch-warning">
              “Changes from” and “Merge into” must use different branches.
            </p>
          )}
          {comparisonLoading && (
            <p className="branch-comparison">Checking branch differences…</p>
          )}
          {comparison && comparison.ahead_by === 0 && (
            <div className="branch-empty-state">
              <GitPullRequest />
              <div>
                <strong>No changes to open yet</strong>
                <p>
                  <code>{headBranch}</code> currently has no commits beyond{" "}
                  <code>{baseBranch}</code>. {blocker ? "Implement the blocker fix" : "Implement the milestone"} and push a commit to the suggested branch, then check again.
                </p>
              </div>
              <button
                className="button button-secondary button-small"
                disabled={comparisonLoading}
                onClick={() => setComparisonRevision((value) => value + 1)}
              >
                <RefreshCw />
                Refresh
              </button>
            </div>
          )}
          {comparison && comparison.ahead_by > 0 && (
            <p className="branch-ready">
              Ready to review: {comparison.ahead_by}{" "}
              {comparison.ahead_by === 1 ? "commit" : "commits"} ahead of the
              base branch.
            </p>
          )}
          {comparisonError && (
            <p className="pull-request-error">{comparisonError}</p>
          )}
          {message && <p className="branch-success">{message}</p>}
          {comparison && comparison.ahead_by > 0 && (
            <button
              className="button button-primary button-small"
              disabled={creatingPullRequest || comparisonLoading}
              onClick={createPullRequest}
            >
              <GitPullRequest />
              {creatingPullRequest ? "Creating pull request…" : "Create pull request"}
            </button>
          )}
        </>
      )}
      {error && <p className="pull-request-error">{error}</p>}
    </div>
  );
}

export default App;
