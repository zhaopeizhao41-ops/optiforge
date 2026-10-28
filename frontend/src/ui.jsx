import React, { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import { X } from 'lucide-react';

export const spring = { type: 'spring', stiffness: 390, damping: 32 };

export function Button({ children, className = '', onClick, ...props }) {
  const reduced = useReducedMotion();
  return <motion.button type="button" className={`studio-button cursor-interaction ${className}`}
    whileHover={reduced ? undefined : { y: -1, scale: 1.015 }}
    whileTap={reduced ? undefined : { scale: .955 }} transition={spring}
    onClick={onClick} {...props}>{children}</motion.button>;
}

export function useBox() {
  const ref = useRef(null);
  const [box, setBox] = useState({ width: 640, height: 400 });
  useLayoutEffect(() => {
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) setBox({ width, height });
    });
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, box];
}

export function Modal({ title, children, onClose, className = '' }) {
  const dialog = useRef(null);
  const previouslyFocused = useRef(null);
  const titleId = useId();
  useEffect(() => {
    previouslyFocused.current = document.activeElement;
    dialog.current?.showModal();
    const element = dialog.current;
    return () => { element?.close(); previouslyFocused.current?.focus(); };
  }, []);
  return <dialog className={`studio-dialog ${className}`} ref={dialog} aria-labelledby={titleId}
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClick={(event) => { if (event.target === dialog.current) { const b = dialog.current.getBoundingClientRect(); if (event.clientX < b.left || event.clientX > b.right || event.clientY < b.top || event.clientY > b.bottom) onClose(); } }}>
    <motion.div initial={{ opacity: 0, y: 12, scale: .97 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={spring}>
      <header><h2 id={titleId}>{title}</h2><Button aria-label="关闭弹窗" className="studio-icon-button" onClick={onClose}><X size={18} /></Button></header>
      {children}
    </motion.div>
  </dialog>;
}
