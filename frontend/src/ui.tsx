import {
  AlertTriangle,
  Check,
  CircleDashed,
  Clock3,
  Github,
  LoaderCircle,
  Lock,
  Search,
  X,
} from "lucide-react";
import { useEffect, useState, type PropsWithChildren, type ReactNode } from "react";

import type { StepState } from "./types";

export function StateForgeLogo({ compact = false }: { compact?: boolean }) {
  return (
    <div className="brand-lockup">
      <img
        alt=""
        className="brand-logo"
        src="/assets/stateforge/svg/stateforge-mark.svg"
      />
      {!compact && (
        <div>
          <strong>StateForge</strong>
          <span>Development Progress Tracking</span>
        </div>
      )}
    </div>
  );
}

const statusIcons: Record<StepState, ReactNode> = {
  planned: <CircleDashed />,
  blocked: <Lock />,
  ready: <Check />,
  pending: <Clock3 />,
  in_review: <Github />,
  completed: <Check />,
  cancelled: <X />,
};

export function StatusBadge({ status }: { status: StepState }) {
  return (
    <span className={`status status-${status}`}>
      {statusIcons[status]}
      {status.replace("_", " ")}
    </span>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function Modal({
  title,
  description,
  onClose,
  children,
  wide = false,
  extraWide = false,
}: PropsWithChildren<{
  title: string;
  description?: string;
  onClose: () => void;
  wide?: boolean;
  extraWide?: boolean;
}>) {
  const [closing, setClosing] = useState(false);

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

  return (
    <div
      className={closing ? "modal-backdrop popup-closing" : "modal-backdrop"}
      role="presentation"
      onMouseDown={requestClose}
    >
      <section
        aria-modal="true"
        className={`modal ${wide ? "modal-wide" : ""} ${extraWide ? "modal-extra-wide" : ""}`}
        role="dialog"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <h2>{title}</h2>
            {description && <p>{description}</p>}
          </div>
          <button aria-label="Close" className="icon-button" onClick={requestClose}>
            <X />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}

export function EmptyState({
  icon = <CircleDashed />,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span>{icon}</span>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="loading-state">
      <LoaderCircle />
      <span>{label}</span>
    </div>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="error-banner">
      <AlertTriangle />
      <span>{message}</span>
    </div>
  );
}

export function SearchInput({
  value,
  onChange,
  placeholder = "Search",
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="search-input">
      <Search />
      <input
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        value={value}
      />
    </label>
  );
}
