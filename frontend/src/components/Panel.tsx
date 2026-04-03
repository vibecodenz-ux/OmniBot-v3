import type { PropsWithChildren, ReactNode } from "react";

interface PanelProps extends PropsWithChildren {
  eyebrow?: string;
  title: string;
  note?: ReactNode;
  actions?: ReactNode;
  className?: string;
  bodyClassName?: string;
}

export function Panel({ eyebrow, title, note, actions, className, bodyClassName, children }: PanelProps) {
  return (
    <section className={["panel-surface", className].filter(Boolean).join(" ")}>
      <header className="panel-header">
        <div className="panel-copy">
          {eyebrow ? <p className="panel-eyebrow">{eyebrow}</p> : null}
          <h2>{title}</h2>
        </div>
        {note ? <div className="panel-note">{note}</div> : null}
        {actions ? <div className="panel-actions">{actions}</div> : null}
      </header>
      <div className={["panel-body", bodyClassName].filter(Boolean).join(" ")}>{children}</div>
    </section>
  );
}