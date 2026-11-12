import React, { useEffect, useId, useRef, useState } from 'react';
import { AnimatePresence, motion, useIsPresent, useReducedMotion } from 'motion/react';
import { Check, ChevronDown } from 'lucide-react';
import { Button, spring } from './ui.jsx';
import { isOptionEnabled, nextEnabledIndex, resolveActiveIndex } from './surface-picker-options.js';
import './surface-picker.css';

function PickerMenu(props) {
  const present = useIsPresent();
  return <motion.div {...props} tabIndex={-1} inert={!present} aria-hidden={present ? undefined : true} />;
}

export function SurfacePicker({ options, value, onChange, label = '选择表面' }) {
  const [open, setOpen] = useState(false);
  const [activeChoice, setActiveChoice] = useState(null);
  const [placement, setPlacement] = useState({ side: 'below', height: 288 });
  const container = useRef(null);
  const list = useRef(null);
  const id = useId();
  const reduced = useReducedMotion();
  const selected = options.findIndex((option) => option.value === value);
  const current = options[selected];
  const active = resolveActiveIndex(options, activeChoice);
  // Persist a fallback in the same render so re-enabling an old row cannot revive it.
  if (open && (active === -1 ? activeChoice !== null
    : activeChoice === null || !Object.is(activeChoice.value, options[active].value))) {
    setActiveChoice(active === -1 ? null : { value: options[active].value });
  }

  function measurePlacement() {
    const element = container.current;
    if (!element) return null;
    const rect = element.getBoundingClientRect();
    const viewport = window.visualViewport;
    const viewportTop = viewport?.offsetTop || 0;
    let top = viewportTop + 20;
    let bottom = viewportTop + (viewport?.height || window.innerHeight) - 20;
    // An absolute menu also belongs to each ancestor's scrollport. Its z-index
    // cannot escape a dialog's overflow, so intersect all visible boundaries.
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      if (!['auto', 'scroll', 'hidden', 'clip'].includes(getComputedStyle(parent).overflowY)) continue;
      const bounds = parent.getBoundingClientRect();
      const innerTop = bounds.top + parent.clientTop;
      top = Math.max(top, innerTop);
      bottom = Math.min(bottom, innerTop + parent.clientHeight);
    }
    const below = Math.max(0, bottom - rect.bottom - 8);
    const above = Math.max(0, rect.top - top - 8);
    const side = below < 220 && above > below ? 'above' : 'below';
    const available = side === 'above' ? above : below;
    const menuStyle = list.current ? getComputedStyle(list.current) : null;
    const frame = menuStyle ? ['paddingTop', 'paddingBottom', 'borderTopWidth', 'borderBottomWidth']
      .reduce((sum, key) => sum + (parseFloat(menuStyle[key]) || 0), 0) : 14;
    if (available <= frame) return null;
    return { side, height: Math.min(288, available) };
  }
  function place(next) {
    setPlacement(previous => previous.side === next.side && previous.height === next.height ? previous : next);
  }
  function show() {
    const next = measurePlacement();
    if (!next) return;
    place(next);
    const position = resolveActiveIndex(options, { value });
    setActiveChoice(position === -1 ? null : { value: options[position].value });
    setOpen(true);
  }
  useEffect(() => {
    if (!open) return;
    const dismiss = (event) => { if (!container.current?.contains(event.target)) setOpen(false); };
    document.addEventListener('pointerdown', dismiss);
    return () => document.removeEventListener('pointerdown', dismiss);
  }, [open]);
  useEffect(() => {
    if (open) list.current?.querySelector(`[data-position="${active}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [active, open]);
  useEffect(() => {
    if (!open) return;
    const reposition = (event) => {
      // Scrolling options changes neither the trigger nor the clipping bounds.
      if (event?.type === 'scroll' && event.target instanceof Node && list.current?.contains(event.target)) return;
      const next = measurePlacement();
      if (next) place(next); else setOpen(false);
    };
    const frame = requestAnimationFrame(reposition);
    const viewport = window.visualViewport;
    window.addEventListener('resize', reposition);
    window.addEventListener('scroll', reposition, true);
    viewport?.addEventListener('resize', reposition);
    viewport?.addEventListener('scroll', reposition);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', reposition);
      window.removeEventListener('scroll', reposition, true);
      viewport?.removeEventListener('resize', reposition);
      viewport?.removeEventListener('scroll', reposition);
    };
  }, [open]);
  function choose(choice) {
    const position = choice === null ? -1 : options.findIndex(option => option.value === choice.value);
    if (!isOptionEnabled(options, position)) return;
    onChange(options[position].value);
    setOpen(false);
  }
  function keyDown(event) {
    if (event.key === 'Tab') { setOpen(false); return; }
    if (event.key === 'Escape') { if (open) event.preventDefault(); setOpen(false); return; }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      if (!open) { show(); return; }
      const currentActive = resolveActiveIndex(options, activeChoice);
      const position = event.key === 'Home' ? nextEnabledIndex(options, -1, 1)
        : event.key === 'End' ? nextEnabledIndex(options, -1, -1)
          : nextEnabledIndex(options, currentActive, event.key === 'ArrowDown' ? 1 : -1);
      setActiveChoice(position === -1 ? null : { value: options[position].value });
    } else if (open && ['Enter', ' '].includes(event.key)) {
      event.preventDefault();
      const currentActive = resolveActiveIndex(options, activeChoice);
      choose(currentActive === -1 ? null : { value: options[currentActive].value });
    }
  }

  return <div className="studio-surface-picker" ref={container} data-open={open}>
    <Button className="studio-picker-trigger" role="combobox" aria-label={label}
      aria-haspopup="listbox" aria-expanded={open} aria-controls={id}
      aria-activedescendant={open && active !== -1 ? `${id}-${active}` : undefined} title={current?.label}
      onKeyDown={keyDown} onBlur={(event) => { if (!event.currentTarget.parentElement.contains(event.relatedTarget)) setOpen(false); }}
      onClick={() => open ? setOpen(false) : show()}>
      <span className="studio-picker-value">{current?.label}</span>
      <motion.span className="studio-picker-chevron" animate={{ rotate: open ? 180 : 0 }} transition={spring}><ChevronDown size={15} /></motion.span>
    </Button>
    <AnimatePresence>
      {open && <PickerMenu id={id} ref={list} role="listbox" aria-label={label} className="studio-picker-menu"
        data-side={placement.side} style={{ maxHeight: placement.height, transformOrigin: placement.side === 'above' ? 'bottom right' : 'top right' }}
        initial={reduced ? { opacity: 0 } : { opacity: 0, y: placement.side === 'above' ? 6 : -6, scale: .97 }}
        animate={{ opacity: 1, y: 0, scale: 1 }} exit={reduced ? { opacity: 0 } : { opacity: 0, y: -3, scale: .98 }}
        transition={{ duration: reduced ? 0 : .18, ease: [.2, .8, .2, 1] }}
        onPointerDown={(event) => event.preventDefault()}>
        {options.map((option, position) => <div key={`${typeof option.value}:${option.value}`} id={`${id}-${position}`} role="option"
          aria-selected={option.value === value} aria-disabled={option.disabled === true ? true : undefined}
          data-position={position} data-active={position === active}
          className="studio-picker-option" onPointerMove={() => {
            const currentPosition = options.findIndex(item => item.value === option.value);
            if (isOptionEnabled(options, currentPosition)) setActiveChoice({ value: option.value });
          }} onClick={() => choose({ value: option.value })}>
          {position === active && <motion.span className="studio-picker-highlight" layoutId={`${id}-highlight`} transition={spring} />}
          <span className="studio-picker-option-label">{option.label}</span>
          {option.value === value && <Check size={15} />}
        </div>)}
      </PickerMenu>}
    </AnimatePresence>
  </div>;
}
