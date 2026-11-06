import { motion, useIsPresent } from "motion/react";
import type { ReactNode } from "react";

export function Modal({ children, labelledBy }: { children: ReactNode; labelledBy: string }) {
  // While fading out the dialog no longer blocks the table (or its keyboard shortcuts).
  const present = useIsPresent();
  return (
    <motion.div
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/70 p-4 backdrop-blur-sm"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      <motion.div
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
    </motion.div>
  );
}
