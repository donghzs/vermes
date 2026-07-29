declare global {
  interface Window {
    /** Set true by the server only for `vermes dashboard --tui` (or VERMES_DASHBOARD_TUI=1). */
    __vermes_DASHBOARD_EMBEDDED_CHAT__?: boolean;
    /** @deprecated Older injected name; treated as on when true. */
    __vermes_DASHBOARD_TUI__?: boolean;
  }
}

/** True only when the dashboard was started with embedded TUI Chat (`vermes dashboard --tui`). */
export function isDashboardEmbeddedChatEnabled(): boolean {
  if (typeof window === "undefined") return false;
  if (window.__vermes_DASHBOARD_EMBEDDED_CHAT__ === true) return true;
  return window.__vermes_DASHBOARD_TUI__ === true;
}
