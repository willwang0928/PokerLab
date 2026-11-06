import { motion } from "motion/react";
import type { ReactNode } from "react";

export function Modal({ children, labelledBy }: { children: ReactNode; labelledBy: string }) {
  return (
    <motion.div
      className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      <motion.div
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        className="w-full max-w-md rounded-3xl bg-gradient-to-b from-slate-900 to-slate-950 p-6 shadow-2xl ring-1 ring-white/10 sm:p-8"
        initial={{ opacity: 0, y: 20, scale: 0.96 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 10, scale: 0.98 }}
        transition={{ type: "spring", stiffness: 260, damping: 24 }}
      >
        {children}
      </motion.div>
    </motion.div>
  );
}
