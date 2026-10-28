import React, { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion, MotionConfig } from 'motion/react';
import { ArrowLeft, ArrowRight, Check, ChevronLeft, ChevronRight, CircleHelp, FileUp,
  Focus, Maximize2, Minus, Moon, Plus, RefreshCw, SlidersHorizontal, Sun } from 'lucide-react';
import { Button, Modal, spring, useBox } from './ui.jsx';
import { importModel, readSnapshot } from './workbench-api.js';

const format = (value) => value === 'Infinity' ? '∞' : value === '-Infinity' ? '−∞'
  : typeof value === 'number' ? value.toLocaleString('en-US', { useGrouping: false, maximumFractionDigits: 6 }) : '—';
const surfaceName = (surface, last) => surface.index === 0 ? '物面' : surface.index === last ? '像面'
  : surface.comment || (surface.is_stop ? '光阑' : '表面');

function NativeScene({ layout, selected, onSelect, zoom }) {
  if (layout.status !== 'ready') return <div className="studio-live-empty" role="status">
    <p>二维轮廓暂不可用</p><p>{layout.issues.join('；')}</p>
  </div>;
  return <NativeGeometry layout={layout} selected={selected} onSelect={onSelect} zoom={zoom} />;
}

function NativeGeometry({ layout, selected, onSelect, zoom }) {
  const [ref, { width, height }] = useBox();
  const [z0, y0, z1, y1] = layout.bounds;
  const scale = Math.min(Math.max(1, width - 76) / Math.max(z1 - z0, Number.EPSILON),
    Math.max(1, height - 70) / Math.max(y1 - y0, Number.EPSILON)) * zoom / 100;
  const centre = zoom > 100 ? layout.surfaces.find((surface) => surface.index === selected)?.vertex_z ?? (z0 + z1) / 2 : (z0 + z1) / 2;
  const x = (z) => width / 2 + (z - centre) * scale;
  const y = (v) => height / 2 - v * scale;
  const path = (points) => points.map(([z, v], i) => `${i ? 'L' : 'M'}${x(z)} ${y(v)}`).join(' ');
  return <div className="studio-scene" ref={ref}>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`真实处方 YZ 轮廓，横纵同尺度，单位 ${layout.units}；当前 S${selected}。未绘制光线。`}>
      <line x1="16" x2={width - 16} y1={y(0)} y2={y(0)} className="studio-axis" strokeDasharray="3 7" />
      {layout.elements.map((element) => <path key={element.front} d={`${path(element.points)} Z`} className="studio-native-glass" />)}
      {layout.surfaces.map((surface) => <g key={surface.index}>
        <path d={path(surface.points)} className={surface.is_image ? 'studio-image-plane' : 'studio-native-surface'}
          style={{ stroke: selected === surface.index ? 'var(--st-text)' : undefined, strokeWidth: selected === surface.index ? 2.4 : undefined }} />
        <path d={path(surface.points)} className="studio-native-hit cursor-interaction" onClick={() => onSelect(surface.index)} />
        {surface.is_stop && !surface.is_image && <>
          <line x1={x(surface.vertex_z)} x2={x(surface.vertex_z)} y1={y(surface.points[0][1])} y2={y(surface.points[0][1]) + 9} className="studio-native-stop" />
          <line x1={x(surface.vertex_z)} x2={x(surface.vertex_z)} y1={y(surface.points.at(-1)[1])} y2={y(surface.points.at(-1)[1]) - 9} className="studio-native-stop" />
        </>}
        {surface.is_image && <text x={x(surface.vertex_z)} y={y(surface.points[0][1]) + 26} textAnchor="middle" className="studio-scene-label">像面</text>}
      </g>)}
    </svg>
  </div>;
}

function NativeInspector({ model, selected, onSelect, onDetails }) {
  const position = model.surfaces.findIndex((surface) => surface.index === selected);
  const surface = model.surfaces[position];
  if (!surface) return null;
  const last = model.surfaces.at(-1).index;
  const step = (delta) => onSelect(model.surfaces[(position + delta + model.surfaces.length) % model.surfaces.length].index);
  return <aside className="studio-inspector" aria-label="表面属性">
    <div className="studio-section-title">表面属性</div>
    <div className="studio-object-heading">
      <select className="studio-native-picker" value={selected} aria-label="选择表面" onChange={(event) => onSelect(Number(event.target.value))}>
        {model.surfaces.map((item) => <option key={item.index} value={item.index}>S{item.index} · {surfaceName(item, last)}</option>)}
      </select>
      <div className="studio-stepper"><Button className="studio-icon-button" aria-label="上一个表面" onClick={() => step(-1)}><ChevronLeft size={17} /></Button>
        <Button className="studio-icon-button" aria-label="下一个表面" onClick={() => step(1)}><ChevronRight size={17} /></Button></div>
    </div>
    <div className="studio-surface-name">{surfaceName(surface, last)}</div>
    <dl className="studio-properties" aria-live="polite">
      {[['曲率半径', format(surface.radius), model.units], ['面后厚度', format(surface.thickness), model.units], ['材料', surface.material || 'AIR', null]].map(([label, value, unit]) => <div key={label}>
        <dt>{label}</dt><dd className="studio-property-value"><AnimatePresence mode="popLayout" initial={false}>
          <motion.span key={`${label}-${value}`} initial={{ y: 7, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: -7, opacity: 0 }} transition={{ duration: .15 }}>{value}</motion.span>
        </AnimatePresence>{unit && <span className="studio-unit">{unit}</span>}</dd>
      </div>)}
    </dl>
    <Button className="studio-details-button" onClick={onDetails}>更多属性<ArrowRight size={16} /></Button>
  </aside>;
}

function ModelDetails({ snapshot, selected }) {
  const model = snapshot.model;
  const surface = model.surfaces.find((item) => item.index === selected);
  return <>
    <dl className="studio-detail-list">
      {[['面型', surface.type], ['半口径 / ' + model.units, format(surface.semi_diameter)], ['圆锥系数', format(surface.conic)],
        ['光阑', surface.is_stop ? '是' : '否'], ['半径求解', surface.radius_solve], ['厚度求解', surface.thickness_solve],
        ['系统单位', model.units], ['配置', `${model.configuration} / ${model.configuration_count}`],
        ['孔径类型', model.settings.aperture_type.split('.').at(-1)], ['孔径值', format(model.settings.aperture_value)],
        ['视场类型', model.settings.field_type.split('.').at(-1)], ['光线瞄准', model.settings.ray_aiming.split('.').at(-1)]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
    </dl>
    <h3 className="studio-data-heading">视场</h3><div className="studio-data-table"><table><thead><tr><th>序号</th><th>X</th><th>Y</th><th>权重</th></tr></thead><tbody>
      {model.fields.map((field) => <tr key={field.index}><td>{field.index}</td><td>{format(field.x)}</td><td>{format(field.y)}</td><td>{format(field.weight)}</td></tr>)}
    </tbody></table></div>
    <h3 className="studio-data-heading">波长</h3><div className="studio-data-table"><table><thead><tr><th>序号</th><th>μm</th><th>权重</th><th>主波长</th></tr></thead><tbody>
      {model.wavelengths.map((wave) => <tr key={wave.index}><td>{wave.index}</td><td>{format(wave.wavelength_um)}</td><td>{format(wave.weight)}</td><td>{wave.is_primary ? '是' : '—'}</td></tr>)}
    </tbody></table></div>
  </>;
}

export default function Workbench() {
  const [snapshot, setSnapshot] = useState(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState(1);
  const [page, setPage] = useState('design');
  const [zoom, setZoom] = useState(100);
  const [focus, setFocus] = useState(false);
  const [modal, setModal] = useState(null);
  const [reduceMotion, setReduceMotion] = useState(false);
  const [theme, setTheme] = useState(() => { try { return localStorage.getItem('optiforge-theme') || 'system'; } catch { return 'system'; } });
  const [systemDark, setSystemDark] = useState(window.matchMedia('(prefers-color-scheme: dark)').matches);
  const file = useRef(null);
  const generation = useRef(0);
  const resolvedTheme = theme === 'system' ? systemDark ? 'dark' : 'light' : theme;
  const model = snapshot?.model;
  const last = model?.surfaces.at(-1).index;

  async function load(operation) {
    const request = ++generation.current;
    setBusy(true); setError('');
    try {
      const result = await operation();
      if (request !== generation.current) return;
      setSnapshot(result);
      setSelected((current) => result.model.surfaces.some((surface) => surface.index === current) ? current : result.model.surfaces[0].index);
    } catch (failure) {
      if (request !== generation.current) return;
      setSnapshot(null); setError(failure.message || '连接本地服务失败');
    } finally { if (request === generation.current) setBusy(false); }
  }
  useEffect(() => { load(readSnapshot); return () => { generation.current += 1; }; }, []);
  useEffect(() => {
    const query = window.matchMedia('(prefers-color-scheme: dark)');
    const change = () => setSystemDark(query.matches);
    query.addEventListener('change', change);
    return () => query.removeEventListener('change', change);
  }, []);
  useEffect(() => {
    const root = document.getElementById('optiforge-soft-studio');
    root.dataset.theme = resolvedTheme; root.style.colorScheme = resolvedTheme;
    root.dataset.reducedMotion = String(reduceMotion);
    try { localStorage.setItem('optiforge-theme', theme); } catch { /* Local storage may be disabled. */ }
  }, [theme, resolvedTheme, reduceMotion]);

  const physical = model?.surfaces.filter((surface) => surface.index > 0 && surface.index < last) || [];
  const around = Math.max(0, physical.findIndex((surface) => surface.index === selected) - 2);
  const chips = physical.slice(Math.min(around, Math.max(0, physical.length - 5)), Math.min(around, Math.max(0, physical.length - 5)) + 5);

  return <MotionConfig reducedMotion={reduceMotion ? 'always' : 'user'} transition={spring}>
    <div className="studio-shell">
      <header className="studio-topbar"><div className="studio-brand"><span className="studio-brand-mark" aria-hidden="true" />OptiForge</div>
        <nav className="studio-navigation" aria-label="主要页面">{[['design', '光路'], ['analysis', '分析']].map(([id, label]) => <Button key={id} aria-pressed={page === id} onClick={() => setPage(id)}>
          {page === id && <motion.span className="studio-nav-highlight" layoutId="native-nav" />}<span>{label}</span>
        </Button>)}</nav>
        <div className="studio-header-actions"><Button className="studio-icon-button" aria-label={resolvedTheme === 'dark' ? '切换浅色主题' : '切换深色主题'} onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}>{resolvedTheme === 'dark' ? <Sun size={19} /> : <Moon size={19} />}</Button>
          <Button className="studio-icon-button" aria-label="外观与动效设置" onClick={() => setModal('appearance')}><SlidersHorizontal size={18} /></Button></div>
      </header>
      <input type="file" ref={file} accept=".zmx,.zos" hidden aria-label="选择光学模型文件" onChange={(event) => {
        const chosen = event.target.files[0]; event.target.value = ''; if (chosen) { setPage('design'); setZoom(100); load(() => importModel(chosen)); }
      }} />
      <AnimatePresence mode="wait" initial={false}>{page === 'design' ? <motion.main key="design" className="studio-design-page" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}>
        <div className="studio-page-heading studio-live-heading"><h1>{model?.name || '光学工作区'}</h1><Button className="studio-open-button" onClick={() => file.current.click()} disabled={busy}><FileUp size={17} />打开模型</Button></div>
        {error && <div className="studio-live-error" role="alert">{error}</div>}
        <div className={`studio-design-board ${focus ? 'is-focused' : ''} ${busy ? 'is-syncing' : ''}`}>
          <div className="studio-canvas-panel"><div className="studio-canvas-top"><span>2D 光路</span>{model && <Button className="studio-model-info" aria-label="系统设置" onClick={() => setModal('details')}>{model.units}</Button>}</div>
            {snapshot ? <NativeScene layout={snapshot.layout} selected={selected} onSelect={setSelected} zoom={zoom} />
              : <div className="studio-live-empty" role="status"><p>{busy ? '正在读取模型' : error ? '模型未连接' : '打开一个光学模型'}</p></div>}
            <div className="studio-surface-chips" aria-label="光学表面">{chips.map((surface) => <Button key={surface.index} aria-pressed={selected === surface.index} onClick={() => setSelected(surface.index)}>
              {selected === surface.index && <motion.span className="studio-chip-highlight" layoutId="native-surface" />}<span>S{surface.index}</span><span>{surfaceName(surface, last)}</span>
            </Button>)}</div>
            <div className="studio-canvas-bottom"><div className="studio-canvas-tools">
              <Button className="studio-icon-button" aria-label="缩小光路" disabled={zoom <= 50 || !model} onClick={() => setZoom(Math.max(50, zoom - 25))}><Minus size={16} /></Button>
              <Button className="studio-zoom-value" aria-label="重置缩放为百分之百" onClick={() => setZoom(100)}>{zoom}%</Button>
              <Button className="studio-icon-button" aria-label="放大光路" disabled={zoom >= 400 || !model} onClick={() => setZoom(Math.min(400, zoom + 25))}><Plus size={16} /></Button>
              <span className="studio-tool-divider" /><Button className="studio-icon-button" aria-label="适应视图" onClick={() => setZoom(100)}><Focus size={17} /></Button>
            </div><Button className="studio-icon-button" aria-label={focus ? '恢复表面属性' : '聚焦光路'} aria-pressed={focus} onClick={() => setFocus(!focus)}><Maximize2 size={16} /></Button></div>
          </div>
          {!focus && (model ? <NativeInspector model={model} selected={selected} onSelect={setSelected} onDetails={() => setModal('details')} /> : <aside className="studio-inspector" aria-label="表面属性"><div className="studio-section-title">表面属性</div></aside>)}
          <div className="studio-sync-aura" aria-hidden="true" />
        </div>
        <div className="studio-workspace-footer"><Button className="studio-agent-pill" onClick={() => load(readSnapshot)} disabled={busy} aria-label="刷新真实模型"><RefreshCw size={16} className={busy ? 'studio-refresh-active' : ''} /><span aria-live="polite">{busy ? '正在读取' : '刷新模型'}</span></Button>
          <Button className="studio-icon-button studio-about" aria-label="关于工作台" onClick={() => setModal('about')}><CircleHelp size={17} /></Button></div>
      </motion.main> : <motion.section key="analysis" className="studio-analysis-page" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
        <div className="studio-analysis-title"><Button className="studio-icon-button studio-back" aria-label="返回光路" onClick={() => setPage('design')}><ArrowLeft size={19} /></Button><h1>像质分析</h1></div>
        <div className="studio-analysis-card studio-analysis-pending"><p>真实分析将在后续接入</p><Button onClick={() => setPage('design')}>返回光路<ArrowRight size={16} /></Button></div>
      </motion.section>}</AnimatePresence>
      {modal === 'appearance' && <Modal title="外观与动效" onClose={() => setModal(null)}><div className="studio-modal-label">界面主题</div><div className="studio-theme-options">{[['light', '浅色', Sun], ['dark', '深色', Moon], ['system', '跟随系统', Focus]].map(([value, label, Icon]) => <Button key={value} aria-pressed={theme === value} onClick={() => setTheme(value)}><Icon size={19} /><span>{label}</span>{theme === value && <Check size={14} />}</Button>)}</div><label className="studio-motion-option"><span>减少动态效果</span><input type="checkbox" checked={reduceMotion} onChange={(event) => setReduceMotion(event.target.checked)} /></label></Modal>}
      {modal === 'details' && model?.surfaces.some((surface) => surface.index === selected) && <Modal className="studio-model-dialog" title={`S${selected} · ${surfaceName(model.surfaces.find((surface) => surface.index === selected), last)}`} onClose={() => setModal(null)}><ModelDetails snapshot={snapshot} selected={selected} /></Modal>}
      {modal === 'about' && <Modal title="本地光学工作台" onClose={() => setModal(null)}><p className="studio-about-copy">处方和二维轮廓来自当前 Zemax 模型。当前为只读显示，光线与分析尚未接入。</p><p className="studio-about-copy">对话继续在外部 Agent 中进行。打开的文件会复制到本地项目目录。</p>{snapshot && <dl className="studio-detail-list"><div><dt>模型版本</dt><dd>{snapshot.revision}</dd></div><div><dt>读取时间</dt><dd>{new Date(snapshot.observed_at).toLocaleTimeString()}</dd></div></dl>}<Button className="studio-modal-done" onClick={() => setModal(null)}>知道了<Check size={16} /></Button></Modal>}
    </div>
  </MotionConfig>;
}
