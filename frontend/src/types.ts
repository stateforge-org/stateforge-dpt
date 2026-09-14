export type Session = {
  authenticated: boolean;
  user: null | {
    id: number;
    login: string;
    name: string | null;
    avatar_url: string | null;
  };
};

export type DeviceAuthorization = {
  authorization_id: string;
  user_code: string;
  verification_uri: string;
  expires_at: string;
  interval: number;
};

export type DeviceAuthorizationPoll = {
  status: "pending" | "complete";
  retry_after?: number;
  user?: Session["user"];
};

export type Project = {
  project_id: string;
  slug: string;
  name: string;
  release: string;
  generated_filename: string;
  plan_source_branch: string;
  revision: number;
  plan_fingerprint?: string | null;
  repository_count?: number;
  step_count?: number;
  updated_at: string;
};

export type CloneState = {
  path: string;
  branch: string;
  origin: string;
  dirty: boolean;
  ahead: number;
  behind: number;
};

export type Repository = {
  repository_id: string;
  project_id: string;
  alias: string;
  github_full_name: string;
  github_default_branch: string | null;
  clone_url: string | null;
  local_path: string | null;
  responsibility: string;
  last_generated_digest: string | null;
  clone_state: CloneState | null;
  clone_error: string | null;
};

export type GitHubAccount = {
  login: string;
  avatar_url: string | null;
  html_url: string | null;
};

export type GitHubTimelineItem = {
  timeline_id: string;
  event: string;
  actor: GitHubAccount | null;
  body: string | null;
  created_at: string | null;
  html_url: string | null;
  state: string | null;
  commit_sha: string | null;
  commit_message: string | null;
  label: string | null;
  assignee: string | null;
  requested_reviewer: string | null;
  rename_from: string | null;
  rename_to: string | null;
  path: string | null;
  line: number | null;
  diff_hunk: string | null;
};

export type Artifact = {
  artifact_id: string;
  kind: "issue" | "pull_request";
  number: number;
  title: string;
  body: string | null;
  state: "open" | "closed";
  html_url: string;
  author_login: string | null;
  timeline: GitHubTimelineItem[];
  assignees: GitHubAccount[];
  reviewers: GitHubAccount[];
  labels: string[];
  head_branch: string | null;
  base_branch: string | null;
  is_draft: number;
  is_merged: number;
  checks_state: string | null;
  updated_at: string;
};

export type StepState =
  | "planned"
  | "blocked"
  | "ready"
  | "pending"
  | "in_review"
  | "completed"
  | "cancelled";

export type Step = {
  step_id: string;
  repository_id: string;
  repository_alias: string;
  github_full_name: string;
  release: string;
  title: string;
  required_result: string;
  branch_name: string;
  computed_state: StepState;
  observed_state: StepState | null;
  dependencies_complete: boolean;
  sort_order: number;
  depends_on: string[];
  artifacts: Artifact[];
};

export type ReconciliationFinding = {
  kind: string;
  severity: "warning" | "error";
  step_id: string;
  message: string;
};

export type Dashboard = {
  project: Project;
  repositories: Repository[];
  steps: Step[];
  metrics: Record<StepState, number> & {
    actionable: number;
    findings: number;
  };
  findings: ReconciliationFinding[];
};

export type Activity = {
  activity_id: number;
  kind: string;
  summary: string;
  details: Record<string, unknown>;
  actor: string | null;
  created_at: string;
};

export type GitHubRepository = {
  id: number;
  full_name: string;
  private: boolean;
  default_branch: string;
  clone_url: string;
  updated_at: string;
  owner_avatar_url: string;
};

export type ProjectImportInspection = {
  project: {
    slug: string;
    name: string;
    release: string;
    generated_filename: string;
    revision: number;
    plan_source_branch: string | null;
  };
  repositories: number;
  steps: number;
  source_repository: string;
  source_path: string;
  fingerprint: string;
};

export type PlanSource = {
  repository_id: string;
  alias: string;
  github_full_name: string;
  path: string | null;
  source_kind: "worktree" | "remote";
  source_branch: string | null;
  revision: number | null;
  fingerprint: string | null;
  status:
    | "unavailable"
    | "missing"
    | "invalid"
    | "current"
    | "newer"
    | "older"
    | "conflict";
  error: string | null;
};

export type PlanSyncStatus = {
  local_revision: number;
  working_fingerprint: string;
  published_fingerprint: string | null;
  has_local_changes: boolean;
  conflict: boolean;
  recommended_repository_id: string | null;
  sources: PlanSource[];
};
