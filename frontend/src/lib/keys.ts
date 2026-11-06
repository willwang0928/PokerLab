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

/** Whether the player's most recent input was the keyboard (rather than a pointer or touch). */
let keyboardInput = false;
if (typeof window !== "undefined") {
  window.addEventListener("keydown", () => (keyboardInput = true), true);
  window.addEventListener("pointerdown", () => (keyboardInput = false), true);
}
export function usingKeyboard(): boolean {
  return keyboardInput;
}

/**
 * Put focus on ``el`` when controls reappear, but only for keyboard players whose focus
 * was lost (it fell back to the page when the previous controls went away). Pointer and
 * touch players are left alone so no stray focus ring appears.
 */
export function restoreFocus(el: HTMLElement | null): void {
  const active = document.activeElement;
  if (el && usingKeyboard() && (!active || active === document.body)) el.focus({ preventScroll: true });
}
