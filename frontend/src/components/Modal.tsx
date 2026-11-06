import { motion, useIsPresent } from "motion/react";
import { type ReactNode, useEffect, useRef } from "react";
import { createPortal } from "react-dom";

/** Dialogs currently open; the app behind them is inert while this is above zero. */
let openCount = 0;

function setAppInert(inert: boolean) {
  const root = document.getElementById("root");
  if (!root) return;
  if (inert) root.setAttribute("inert", "");
  else root.removeAttribute("inert");
}

const FOCUSABLE = 'input:not([disabled]), button:not([disabled]), [href], select, textarea, [tabindex]:not([tabindex="-1"])';

/**
 * A modal dialog. It renders outside the app root, makes the app behind it inert (so
 * Tab, clicks and screen readers stay inside the dialog), moves focus into it, closes on
 * Escape when ``onClose`` is given, and puts focus back where it was afterwards.
 */
export function Modal({
  children,
  labelledBy,
  onClose,
}: {
  children: ReactNode;
  labelledBy: string;
  onClose?: () => void;
}) {
  // While fading out the dialog no longer blocks the table (or its keyboard shortcuts).
  const present = useIsPresent();
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!present) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    openCount++;
    setAppInert(true);
    const dialog = dialogRef.current;
    const first = dialog?.querySelector<HTMLElement>("[data-autofocus]") ?? dialog?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && onCloseRef.current) {
        e.preventDefault();
        onCloseRef.current();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      openCount = Math.max(0, openCount - 1);
      if (openCount === 0) setAppInert(false);
      // Return focus unless something else has already taken it.
      if (previous?.isConnected && (!document.activeElement || document.activeElement === document.body || dialog?.contains(document.activeElement))) {
        previous.focus();
      }
    };
  }, [present]);

  return createPortal(
    <motion.div
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/70 p-4 backdrop-blur-sm"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      <motion.div
        ref={dialogRef}
        role="dialog"
        aria-modal={present ? "true" : undefined}
        aria-labelledby={labelledBy}
        className="relative w-full max-w-md overflow-hidden rounded-3xl bg-gradient-to-b from-[#18212d] to-[#0c1118] p-6 shadow-[0_40px_100px_-30px_rgba(0,0,0,.9)] ring-1 ring-white/10 sm:p-8"
        initial={{ opacity: 0, y: 20, scale: 0.96 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 10, scale: 0.98 }}
        transition={{ type: "spring", stiffness: 260, damping: 24 }}
      >
        <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-amber-200/40 to-transparent" />
        {children}
      </motion.div>
    </motion.div>,
    document.body,
  );
}
