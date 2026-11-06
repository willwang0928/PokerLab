/**
 * Whether a global keyboard shortcut should be ignored for this key press: the player is
 * typing in a field, a dialog is open over the table, the key is auto-repeating, or a
 * modifier is held (so browser shortcuts like Cmd+R keep working).
 */
export function shortcutBlocked(e: KeyboardEvent): boolean {
  if (e.repeat || e.metaKey || e.ctrlKey || e.altKey) return true;
  const t = e.target;
  if (t instanceof HTMLElement) {
    if (t.isContentEditable || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement) return true;
    if (t instanceof HTMLInputElement && !["checkbox", "radio", "range", "button"].includes(t.type)) return true;
  }
  return typeof document !== "undefined" && document.querySelector('[role="dialog"][aria-modal="true"]') !== null;
}
