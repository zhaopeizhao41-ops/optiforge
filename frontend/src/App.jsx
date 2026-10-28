import React, { useEffect, useId, useRef, useState } from 'react';
import { AnimatePresence, motion, MotionConfig, useReducedMotion } from 'motion/react';
import {
  ArrowLeft, ArrowRight, Check, ChevronDown, ChevronLeft, ChevronRight,
  CircleHelp, Focus, Maximize2, Minus, Moon, Plus, SlidersHorizontal,
  Sun, Waves, X,
} from 'lucide-react';
import { surfaces, mtfSamples, fieldSamples, wavelengthSamples } from './fixtures.js';
import { Button, Modal, useBox } from './ui.jsx';
import Workbench from './Workbench.jsx';

const spring = { type: 'spring', stiffness: 390, damping: 32 };
const rootNode = () => document.getElementById('optiforge-soft-studio');

function SyncIndicator({ active = false }) {
  return <span className={`studio-sync-indicator ${active ? 'is-active' : ''}`} aria-hidden="true" />;
}

function FieldLegend({ visible, onToggle }) {
  return <div className="studio-field-legend" aria-label="视场颜色">
    {fieldSamples.map((field) => <Button key={field.id} aria-label={`${field.name}光线`}
      aria-pressed={visible[field.id]} onClick={() => onToggle(field.id)}>
      <span className={`studio-field-swatch is-${field.id}`} style={{ '--field-color': field.color }} />
      {field.name}
    </Button>)}
  </div>;
}

function OpticalScene({ selected, onSelect, rays, zoom, synced, focus, visibleFields }) {
  const [ref, { width: w, height: h }] = useBox();
  const reduced = useReducedMotion();
  const unique = useId().replaceAll(':', '');
  const center = h * .48;
  const half = Math.min(100, h * .29, w * .22);
  const sx = [w * .29, w * .435, w * .56];
  const image = w * .885;
  const bend = [half * .25, -half * (synced ? .24 : .22), -half * .07];
  const surfacePath = (i) => `M ${sx[i] + bend[i]} ${center - half} Q ${sx[i] - bend[i]} ${center} ${sx[i] + bend[i]} ${center + half}`;
  const elementPath = (i) => `${surfacePath(i)} L ${sx[i + 1] + bend[i + 1]} ${center + half} Q ${sx[i + 1] - bend[i + 1]} ${center} ${sx[i + 1] + bend[i + 1]} ${center - half} Z`;
  const ratio = zoom / 100;
  const visibleW = w / ratio;
  const visibleH = h / ratio;
  const labelY = center + half + 35;
  return <div className={`studio-scene ${focus ? 'is-focus' : ''}`} ref={ref}>
    <svg key={`${w}-${h}`} viewBox={`${(w - visibleW) / 2} ${(h - visibleH) / 2} ${visibleW} ${visibleH}`}
      role="img" aria-label={`胶合双片二维示意，选中表面 S${selected}。轴上绿色，0.7 视场红色，边缘青色。非等比例，光线不是追迹结果。`}>
      <defs>
        <linearGradient id={`glass-a-${unique}`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--st-glass-a)" stopOpacity=".84" />
          <stop offset="1" stopColor="var(--st-glass-b)" stopOpacity=".3" />
        </linearGradient>
        <linearGradient id={`glass-b-${unique}`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--st-glass-b)" stopOpacity=".5" />
          <stop offset="1" stopColor="var(--st-glass-a)" stopOpacity=".15" />
        </linearGradient>
      </defs>
      <line x1="25" y1={center} x2={w - 22} y2={center} className="studio-axis" strokeDasharray="3 7" />
      <motion.path initial={false} animate={{ d: elementPath(0) }} transition={{ duration: reduced ? 0 : .65 }}
        fill={`url(#glass-a-${unique})`} className="studio-glass-edge" />
      <motion.path initial={false} animate={{ d: elementPath(1) }} transition={{ duration: reduced ? 0 : .65 }}
        fill={`url(#glass-b-${unique})`} className="studio-glass-edge" />
      <AnimatePresence initial={false}>
        {rays && <motion.g key="rays" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          {fieldSamples.filter((field) => visibleFields[field.id]).map((field) => [-.72, 0, .72].map((offset, i) => {
            const shift = field.value * half * .4;
            const y = center + half * offset;
            const x = (index) => sx[index] + bend[index] * offset * offset;
            const d = `M 24 ${y - shift} L ${x(0)} ${y} L ${x(1)} ${center + half * offset * .88 + shift * .22} L ${x(2)} ${center + half * offset * (synced ? .62 : .64) + shift * .45} L ${image} ${center + shift}`;
            return <motion.path key={`${field.id}-${i}`} initial={false} animate={{ d }} transition={{ duration: reduced ? 0 : .65 }}
              fill="none" stroke={field.color} strokeDasharray={field.dash} strokeWidth={offset === 0 ? 1.5 : 1.15}
              opacity={offset === 0 ? 1 : .8} />;
          }))}
        </motion.g>}
      </AnimatePresence>
      <line x1={image} x2={image} y1={center - half * .72} y2={center + half * .72} className="studio-image-plane" />
      <text x={image} y={labelY} textAnchor="middle" className="studio-scene-label">像面</text>
      {surfaces.map((surface, i) => <g key={surface.id}>
        <motion.path initial={false} animate={{ d: surfacePath(i), opacity: selected === surface.id ? 1 : 0 }}
          transition={{ duration: reduced ? 0 : .25 }} stroke="var(--st-accent)" strokeWidth="2.8" fill="none" />
      </g>)}
    </svg>
    <div className="studio-scene-targets" aria-label="选择光学表面">
      {surfaces.map((surface, i) => <Button key={surface.id} aria-label={`选择曲面 S${surface.id} ${surface.name}`}
        aria-pressed={selected === surface.id} className="studio-surface-hit"
        style={{ left: `${(sx[i] - (w - visibleW) / 2) / visibleW * 100}%`, top: `${(center - (h - visibleH) / 2) / visibleH * 100}%` }}
        onClick={() => onSelect(surface.id)} />)}
    </div>
  </div>;
}

function PropertyValue({ children, suffix }) {
  return <div className="studio-property-value"><AnimatePresence mode="popLayout" initial={false}>
    <motion.span key={String(children)} initial={{ y: 8, opacity: 0 }} animate={{ y: 0, opacity: 1 }}
      exit={{ y: -8, opacity: 0 }} transition={{ duration: .19 }}>{children}</motion.span>
  </AnimatePresence>{suffix && <span className="studio-unit">{suffix}</span>}</div>;
}

function Inspector({ selected, onSelect, synced, onDetails }) {
  const surface = surfaces[selected - 1];
  const radius = selected === 2 && synced ? '−39.800' : surface.radius;
  const [picker, setPicker] = useState(false);
  const pickerRef = useRef(null);
  useEffect(() => {
    if (!picker) return;
    const outside = (event) => { if (!pickerRef.current?.contains(event.target)) setPicker(false); };
    const escape = (event) => { if (event.key === 'Escape') setPicker(false); };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [picker]);
  return <aside className="studio-inspector" aria-label="表面属性">
    <div className="studio-section-title">表面属性</div>
    <div className="studio-object-heading">
      <div className="studio-surface-picker" ref={pickerRef}>
        <Button className="studio-object-button" aria-label="选择表面" aria-expanded={picker}
          onClick={() => setPicker(!picker)}><span>S{selected}</span><ChevronDown size={16} /></Button>
        <AnimatePresence>{picker && <motion.div className="studio-surface-menu"
          initial={{ opacity: 0, y: -5, scale: .97 }} animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -4, scale: .97 }} transition={spring}>
          {surfaces.map((item) => <Button key={item.id} onClick={() => { onSelect(item.id); setPicker(false); }}
            aria-pressed={item.id === selected}><span>S{item.id}</span>{item.name}{item.id === selected && <Check size={15} />}</Button>)}
        </motion.div>}</AnimatePresence>
      </div>
      <div className="studio-stepper">
        <Button className="studio-icon-button" aria-label="上一个表面" onClick={() => onSelect(selected === 1 ? 3 : selected - 1)}><ChevronLeft size={17} /></Button>
        <Button className="studio-icon-button" aria-label="下一个表面" onClick={() => onSelect(selected === 3 ? 1 : selected + 1)}><ChevronRight size={17} /></Button>
      </div>
    </div>
    <AnimatePresence mode="wait" initial={false}><motion.div key={surface.name} className="studio-surface-name"
      initial={{ opacity: 0, x: 6 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -5 }} transition={{ duration: .13 }}>{surface.name}</motion.div></AnimatePresence>
    <dl className="studio-properties">
      <div><dt>曲率半径</dt><dd><PropertyValue suffix="mm">{radius}</PropertyValue></dd></div>
      <div><dt>面后厚度</dt><dd><PropertyValue suffix="mm">{surface.thickness}</PropertyValue></dd></div>
      <div><dt>材料</dt><dd><PropertyValue>{surface.material}</PropertyValue></dd></div>
    </dl>
    <Button className="studio-details-button" onClick={onDetails}>更多属性<ArrowRight size={16} /></Button>
  </aside>;
}

function SpotPlot({ field, wavelengths }) {
  const [ref, { width: w, height: h }] = useBox();
  const left = 46, right = w - 20, top = 20, bottom = h - 48;
  const centerX = (left + right) / 2, centerY = (top + bottom) / 2;
  const scale = Math.max(1, Math.min(right - left, bottom - top) / 28);
  return <section className="studio-spot-pane"><h2>{field.name}</h2><div className="studio-spot-plot" ref={ref}>
    <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`${field.name}点列图示意，三种波长，非计算结果。`}>
      <line x1={left} x2={right} y1={centerY} y2={centerY} className="studio-chart-grid" />
      <line x1={centerX} x2={centerX} y1={top} y2={bottom} className="studio-chart-grid" />
      {[-10, 0, 10].map((v) => <g key={v}><text x={centerX + v * scale} y={bottom + 22} textAnchor="middle">{v}</text><text x={left - 10} y={centerY - v * scale + 5} textAnchor="end">{v}</text></g>)}
      {wavelengthSamples.map((wave, waveIndex) => wavelengths[wave.id] && Array.from({ length: 50 }, (_, i) => {
        const angle = i * 2.39996 + waveIndex * .6;
        const r = Math.sqrt((i + 1) / 50) * (2.5 + field.value * 1.7 + waveIndex * .3);
        const shift = (waveIndex - 1) * (1 + field.value * .8);
        return <motion.circle key={`${wave.id}-${i}`} initial={false}
          animate={{ cx: centerX + (Math.cos(angle) * r + shift) * scale, cy: centerY + Math.sin(angle) * r * scale * (.8 - field.value * .2) }}
          transition={spring} r="1.7" fill={wave.color} opacity=".8" />;
      }))}
      <text x="14" y={(top + bottom) / 2} textAnchor="middle" transform={`rotate(-90 14 ${(top + bottom) / 2})`}>Y / μm</text>
      <text x={(left + right) / 2} y={h - 4} textAnchor="middle">X / μm</text>
    </svg>
  </div></section>;
}

function AnalysisChart({ type, fields, series }) {
  const [ref, { width: w, height: h }] = useBox();
  const left = 58, top = 24, right = w - 24, bottom = h - 52;
  const plotW = Math.max(1, right - left), plotH = Math.max(1, bottom - top);
  let content;
  if (type === 'mtf') {
    const line = (name, field) => mtfSamples.map((point, i) => `${i ? 'L' : 'M'} ${left + plotW * point.frequency / 100} ${top + plotH * (1 - Math.max(0, point[name] - field.value * .16 * point.frequency / 100))}`).join(' ');
    content = <>
      {[0, .5, 1].map((v) => <g key={v}><line x1={left} y1={top + plotH * (1 - v)} x2={right} y2={top + plotH * (1 - v)} className="studio-chart-grid" /><text x={left - 14} y={top + plotH * (1 - v) + 4} textAnchor="end">{v.toFixed(1)}</text></g>)}
      {[0, 50, 100].map((v) => <text key={v} x={left + plotW * v / 100} y={bottom + 22} textAnchor="middle">{v}</text>)}
      {fields.map((field) => <g key={field.id} style={{ '--chart-color': field.color }}>
        {series.t && <motion.path initial={false} animate={{ d: line('t', field) }} className="studio-chart-line" transition={{ duration: .35 }} />}
        {series.s && <motion.path initial={false} animate={{ d: line('s', field) }} className="studio-chart-line" strokeDasharray="6 5" transition={{ duration: .35 }} />}
      </g>)}
      <text x="16" y={(top + bottom) / 2} textAnchor="middle" transform={`rotate(-90 16 ${(top + bottom) / 2})`}>MTF</text>
      <text x={(left + right) / 2} y={h - 8} textAnchor="middle">空间频率 / lp·mm⁻¹</text>
    </>;
  } else {
    const curve = (field) => Array.from({ length: 41 }, (_, i) => {
      const x = i / 20 - 1;
      const y = .08 * (x ** 4 - x ** 2) + field.value * .018 * x;
      return `${i ? 'L' : 'M'} ${left + i / 40 * plotW} ${top + plotH * (.5 - y / .1)}`;
    }).join(' ');
    content = <>
      {[-.05, 0, .05].map((v) => <g key={v}><line x1={left} x2={right} y1={top + plotH * (.5 - v / .1)} y2={top + plotH * (.5 - v / .1)} className="studio-chart-grid" /><text x={left - 12} y={top + plotH * (.5 - v / .1) + 4} textAnchor="end">{v.toFixed(2)}</text></g>)}
      {[-1, 0, 1].map((v) => <text key={v} x={left + (v + 1) / 2 * plotW} y={bottom + 21} textAnchor="middle">{v}</text>)}
      {fields.map((field) => <motion.path key={field.id} initial={false} animate={{ d: curve(field) }} className="studio-chart-line"
        style={{ '--chart-color': field.color }} strokeDasharray={field.dash} transition={{ duration: .35 }} />)}
      <text x="16" y={(top + bottom) / 2} textAnchor="middle" transform={`rotate(-90 16 ${(top + bottom) / 2})`}>OPD / waves</text>
      <text x={(left + right) / 2} y={h - 8} textAnchor="middle">归一化瞳坐标</text>
    </>;
  }
  return <div className="studio-analysis-plot" ref={ref}><svg viewBox={`0 0 ${w} ${h}`} role="img"
    aria-label={`${type === 'mtf' ? 'MTF' : '波前'}多视场交互样例，非计算结果。`}>
    {content}
  </svg></div>;
}

function Analysis({ onBack }) {
  const [type, setType] = useState('spot');
  const [field, setField] = useState('all');
  const [series, setSeries] = useState({ t: true, s: true });
  const [wavelengths, setWavelengths] = useState({ short: true, reference: true, long: true });
  const fields = fieldSamples.filter((item) => field === 'all' || field === item.id);
  return <motion.section className="studio-analysis-page" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }} transition={spring}>
    <div className="studio-analysis-title"><Button className="studio-icon-button studio-back" aria-label="返回光路" onClick={onBack}><ArrowLeft size={19} /></Button><h1>像质分析</h1></div>
    <div className="studio-analysis-card">
      <div className="studio-analysis-toolbar">
        <div className="studio-analysis-tabs" aria-label="选择分析类型">
          {[['spot', '点列图'], ['mtf', 'MTF'], ['wavefront', '波前']].map(([key, label]) => <Button key={key} onClick={() => setType(key)} aria-pressed={type === key}>
            {type === key && <motion.span className="studio-tab-highlight" layoutId="analysis-tab" transition={spring} />}<span>{label}</span></Button>)}
        </div>
        <label className="studio-field-select">视场<select value={field} aria-label="分析示例视场" onChange={(e) => setField(e.target.value)}><option value="all">全部</option>{fieldSamples.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><ChevronDown size={14} /></label>
      </div>
      {type === 'spot' ? <>
        <div className="studio-spot-grid">{fields.map((item) => <SpotPlot key={item.id} field={item} wavelengths={wavelengths} />)}</div>
        <div className="studio-series-controls" aria-label="波长颜色">{wavelengthSamples.map((wave) => <Button key={wave.id} aria-pressed={wavelengths[wave.id]}
          onClick={() => setWavelengths({ ...wavelengths, [wave.id]: !wavelengths[wave.id] })}>
          <span className="studio-wavelength-swatch" style={{ background: wave.color }} />{wave.value} nm
        </Button>)}</div>
      </> : <>
        <AnalysisChart type={type} fields={fields} series={series} />
        <div className="studio-chart-legend" aria-label="视场颜色">{fields.map((item) => <span key={item.id}>
          <span className="studio-field-swatch" style={{ '--field-color': item.color }} />{item.name}
        </span>)}</div>
        {type === 'mtf' && <div className="studio-series-controls">{[['t', 'T 子午'], ['s', 'S 弧矢']].map(([key, label]) => <Button key={key} aria-pressed={series[key]} onClick={() => setSeries({ ...series, [key]: !series[key] })}><span className={`studio-line-sample ${key === 's' ? 'is-dashed' : ''}`} />{label}</Button>)}</div>}
      </>}
    </div>
  </motion.section>;
}

function PreviewApp() {
  const host = rootNode().hasAttribute('data-inline-preview') ? window.openai : undefined;
  const saved = host?.widgetState?.modelContent || {};
  const [page, setPage] = useState('design');
  const [selected, setSelected] = useState([1, 2, 3].includes(saved.selectedSurface) ? saved.selectedSurface : 2);
  const [theme, setTheme] = useState(['system', 'light', 'dark'].includes(saved.theme) ? saved.theme : 'system');
  const [systemDark, setSystemDark] = useState(window.matchMedia('(prefers-color-scheme: dark)').matches);
  const [reduceMotion, setReduceMotion] = useState(false);
  const [rays, setRays] = useState(true);
  const [visibleFields, setVisibleFields] = useState({ axis: true, middle: true, edge: true });
  const [zoom, setZoom] = useState(100);
  const [focus, setFocus] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [synced, setSynced] = useState(false);
  const [syncDone, setSyncDone] = useState(false);
  const [modal, setModal] = useState(null);
  const timer = useRef(null);
  const resolvedTheme = theme === 'system' ? systemDark ? 'dark' : 'light' : theme;
  const reducePreference = useReducedMotion();
  const reduced = reduceMotion || reducePreference;

  useEffect(() => {
    const query = window.matchMedia('(prefers-color-scheme: dark)');
    const listener = () => setSystemDark(query.matches);
    query.addEventListener('change', listener);
    return () => query.removeEventListener('change', listener);
  }, []);
  useEffect(() => {
    rootNode().dataset.theme = resolvedTheme;
    rootNode().dataset.reducedMotion = String(Boolean(reduced));
    rootNode().style.colorScheme = resolvedTheme;
  }, [resolvedTheme, reduced]);
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    const listener = (event) => {
      const state = event.detail?.globals?.widgetState?.modelContent;
      if (state && ['light', 'dark', 'system'].includes(state.theme)) setTheme(state.theme);
    };
    if (host) window.addEventListener('openai:set_globals', listener);
    return () => { if (host) window.removeEventListener('openai:set_globals', listener); };
  }, []);

  function remember(nextTheme = theme, nextSelected = selected) {
    host?.setWidgetState?.({ modelContent: { theme: nextTheme, selectedSurface: nextSelected, uiConcept: '低密度光路与表面属性 · 分析二级页' }, privateContent: {} }).catch(() => {});
  }
  function selectSurface(id) { setSelected(id); remember(theme, id); }
  function changeTheme(value) { setTheme(value); remember(value); }
  function demoSync() {
    if (syncing) return;
    setSyncing(true); setSyncDone(false);
    timer.current = setTimeout(() => {
      setSynced((value) => !value); setSelected(2); setSyncing(false); setSyncDone(true);
      timer.current = setTimeout(() => setSyncDone(false), 2400);
    }, reduced ? 600 : 1700);
  }

  return <MotionConfig reducedMotion={reduceMotion ? 'always' : 'user'} transition={spring}>
    <div className="studio-shell">
      <header className="studio-topbar">
        <div className="studio-brand"><span className="studio-brand-mark" aria-hidden="true" />OptiForge</div>
        <nav className="studio-navigation" aria-label="主要页面">
          {[['design', '光路'], ['analysis', '分析']].map(([key, label]) => <Button key={key} aria-pressed={page === key} onClick={() => setPage(key)}>
            {page === key && <motion.span className="studio-nav-highlight" layoutId="main-nav" transition={spring} />}<span>{label}</span></Button>)}
        </nav>
        <div className="studio-header-actions"><Button className="studio-icon-button" aria-label={resolvedTheme === 'dark' ? '切换浅色主题' : '切换深色主题'}
          onClick={() => changeTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}>
          <AnimatePresence mode="wait" initial={false}><motion.span key={resolvedTheme} initial={{ rotate: -40, opacity: 0, scale: .7 }} animate={{ rotate: 0, opacity: 1, scale: 1 }} exit={{ rotate: 40, opacity: 0, scale: .7 }} transition={{ duration: .15 }}>{resolvedTheme === 'dark' ? <Sun size={19} /> : <Moon size={19} />}</motion.span></AnimatePresence>
        </Button><Button className="studio-icon-button" aria-label="外观与动效设置" onClick={() => setModal('appearance')}><SlidersHorizontal size={18} /></Button></div>
      </header>
      <AnimatePresence mode="wait" initial={false}>
        {page === 'design' ? <motion.main className="studio-design-page" key="design" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={spring}>
          <div className="studio-page-heading"><h1>胶合双片</h1></div>
          <div className={`studio-design-board ${focus ? 'is-focused' : ''} ${syncing ? 'is-syncing' : ''}`}>
            <div className="studio-canvas-panel">
              <div className="studio-canvas-top"><span>2D 光路</span><FieldLegend visible={visibleFields}
                onToggle={(id) => setVisibleFields({ ...visibleFields, [id]: !visibleFields[id] })} /></div>
              <OpticalScene selected={selected} onSelect={selectSurface} rays={rays} zoom={zoom} synced={synced} focus={focus} visibleFields={visibleFields} />
              <div className="studio-surface-chips" aria-label="光学表面">
                {surfaces.map((surface) => <Button key={surface.id} aria-pressed={selected === surface.id} onClick={() => selectSurface(surface.id)}>
                  {selected === surface.id && <motion.span className="studio-chip-highlight" layoutId="surface-chip" transition={spring} />}<span>S{surface.id}</span><span>{surface.name}</span></Button>)}
              </div>
              <div className="studio-canvas-bottom"><div className="studio-canvas-tools">
                <Button className="studio-icon-button" aria-label="缩小光路" disabled={zoom <= 80} onClick={() => setZoom(Math.max(80, zoom - 10))}><Minus size={16} /></Button>
                <Button className="studio-zoom-value" aria-label="重置缩放为百分之百" onClick={() => setZoom(100)}>{zoom}%</Button>
                <Button className="studio-icon-button" aria-label="放大光路" disabled={zoom >= 120} onClick={() => setZoom(Math.min(120, zoom + 10))}><Plus size={16} /></Button>
                <span className="studio-tool-divider" />
                <Button className="studio-icon-button" aria-label="适应视图" onClick={() => { setZoom(100); setRays(true); setVisibleFields({ axis: true, middle: true, edge: true }); }}><Focus size={17} /></Button>
                <Button className="studio-icon-button" aria-label={rays ? '隐藏光线示意' : '显示光线示意'} aria-pressed={rays} onClick={() => setRays(!rays)}><Waves size={17} /></Button>
              </div><Button className="studio-icon-button studio-focus-button" aria-label={focus ? '恢复表面属性' : '聚焦光路'} aria-pressed={focus} onClick={() => setFocus(!focus)}><Maximize2 size={16} /></Button></div>
            </div>
            {!focus && <Inspector selected={selected} onSelect={selectSurface} synced={synced} onDetails={() => setModal('details')} />}
            <div className="studio-sync-aura" aria-hidden="true" />
          </div>
          <div className="studio-workspace-footer">
            <Button className={`studio-agent-pill ${syncing ? 'is-syncing' : ''} ${syncDone ? 'is-done' : ''}`} onClick={demoSync} disabled={syncing}
              aria-label="演示 Agent 同步动效"><SyncIndicator active={syncing} /><span aria-live="polite">{syncing ? '正在同步' : syncDone ? '已更新' : '同步预览'}</span>{syncDone && <Check size={16} />}</Button>
            <Button className="studio-icon-button studio-about" aria-label="关于前端预览" onClick={() => setModal('about')}><CircleHelp size={17} /></Button>
          </div>
        </motion.main> : <Analysis key="analysis" onBack={() => setPage('design')} />}
      </AnimatePresence>

      {modal === 'appearance' && <Modal title="外观与动效" onClose={() => setModal(null)}>
        <div className="studio-modal-label">界面主题</div><div className="studio-theme-options">
          {[['light', '浅色', Sun], ['dark', '深色', Moon], ['system', '跟随系统', Focus]].map(([value, label, Icon]) => <Button key={value} aria-pressed={theme === value} onClick={() => changeTheme(value)}><Icon size={19} /><span>{label}</span>{theme === value && <Check size={14} />}</Button>)}
        </div>
        <label className="studio-motion-option"><span>减少动态效果</span><input type="checkbox" checked={reduceMotion} onChange={(e) => setReduceMotion(e.target.checked)} /></label>
      </Modal>}
      {modal === 'details' && <Modal title={`S${selected} · ${surfaces[selected - 1].name}`} onClose={() => setModal(null)}>
        <dl className="studio-detail-list">
          {[['面型', surfaces[selected - 1].type], ['曲率半径 / mm', selected === 2 && synced ? '−39.800' : surfaces[selected - 1].radius], ['面后厚度 / mm', surfaces[selected - 1].thickness], ['材料', surfaces[selected - 1].material], ['圆锥系数', surfaces[selected - 1].conic]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
        </dl>
      </Modal>}
      {modal === 'about' && <Modal title="前端体验预览" onClose={() => setModal(null)}>
        <p className="studio-about-copy">此页面使用演示数据，光路、分析与同步均为交互预览。</p>
        <p className="studio-about-copy">对话继续在外部 Agent 中进行，计算和实时状态将在后续接入。</p>
        <Button className="studio-modal-done" onClick={() => setModal(null)}>知道了<Check size={16} /></Button>
      </Modal>}
    </div>
  </MotionConfig>;
}

export default function App() {
  const preview = rootNode().hasAttribute('data-inline-preview') || new URLSearchParams(window.location.search).has('preview');
  return preview ? <PreviewApp /> : <Workbench />;
}
