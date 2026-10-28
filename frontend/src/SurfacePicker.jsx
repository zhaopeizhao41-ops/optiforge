import React, { useEffect, useId, useRef, useState } from 'react';
import { AnimatePresence, motion, useIsPresent, useReducedMotion } from 'motion/react';
import { Check, ChevronDown } from 'lucide-react';
import { Button, spring } from './ui.jsx';
import './surface-picker.css';

function PickerMenu(props) {
  const present = useIsPresent();
  return <motion.div {...props} tabIndex={-1} inert={!present} aria-hidden={present ? undefined : true} />;
}

export function SurfacePicker({ options, value, onChange, label = '选择表面' }) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [placement, setPlacement] = useState({ side: 'below', height: 288 });
  const container = useRef(null);
  const list = useRef(null);
  const id = useId();
  const reduced = useReducedMotion();
  const selected = options.findIndex((option) => option.value === value);
  const current = options[selected];

  function show() {
    const rect = container.current.getBoundingClientRect();
    const below = window.innerHeight - rect.bottom - 20;
    const above = rect.top - 20;
    const side = below < 220 && above > below ? 'above' : 'below';
    setPlacement({ side, height: Math.max(96, Math.min(288, side === 'above' ? above : below)) });
    setActive(Math.max(0, selected));
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
  function choose(position) {
    onChange(options[position].value);
    setOpen(false);
  }
  function keyDown(event) {
    if (event.key === 'Tab') { setOpen(false); return; }
    if (event.key === 'Escape') { if (open) event.preventDefault(); setOpen(false); return; }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      if (!open) { show(); return; }
      setActive((position) => event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1
        : (position + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length);
    } else if (open && ['Enter', ' '].includes(event.key)) {
      event.preventDefault(); choose(active);
    }
  }

  return <div className="studio-surface-picker" ref={container} data-open={open}>
    <Button className="studio-picker-trigger" role="combobox" aria-label={label}
      aria-haspopup="listbox" aria-expanded={open} aria-controls={id}
      aria-activedescendant={open ? `${id}-${active}` : undefined} title={current?.label}
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
        {options.map((option, position) => <div key={option.value} id={`${id}-${position}`} role="option"
          aria-selected={option.value === value} data-position={position} data-active={position === active}
          className="studio-picker-option" onPointerMove={() => setActive(position)} onClick={() => choose(position)}>
          {position === active && <motion.span className="studio-picker-highlight" layoutId={`${id}-highlight`} transition={spring} />}
          <span className="studio-picker-option-label">{option.label}</span>
          {option.value === value && <Check size={15} />}
        </div>)}
      </PickerMenu>}
    </AnimatePresence>
  </div>;
}
