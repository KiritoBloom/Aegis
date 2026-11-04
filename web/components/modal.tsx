"use client";

import { useEffect, useRef } from "react";
import styles from "./modal.module.css";

/** Native modal semantics: focus trap, Escape, inert background, focus restore. */
export function Modal({ children, labelId, className = "", onClose }: {
  children: React.ReactNode; labelId: string; className?: string; onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    const previousFocus = document.activeElement;
    dialog?.showModal();
    return () => {
      dialog?.close();
      // React may already have detached the dialog when this cleanup runs.
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) {
        previousFocus.focus({ preventScroll: true });
      }
    };
  }, []);
  return <dialog ref={ref} className={`${styles.base} ${className}`} aria-labelledby={labelId}
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClick={(event) => { if (event.target === event.currentTarget) {
      const box = event.currentTarget.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) onClose();
    } }}>{children}</dialog>;
}
