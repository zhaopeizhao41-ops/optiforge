export const MIN_ZOOM = 10;
export const MAX_ZOOM = 1600;
export const homeCamera = () => ({ zoom: 100, x: 0, y: 0 });
export function frameBounds(previous, current, camera) {
  return current && (!previous || camera.zoom===100 && camera.x===0 && camera.y===0) ? current : previous;
}

// Offsets are screen pixels relative to the centre of the current viewport.
export function zoomCamera(camera, zoom, anchor = { x: 0, y: 0 }) {
  if (![zoom, anchor.x, anchor.y].every(Number.isFinite)) return camera;
  const next = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom));
  const ratio = next / camera.zoom;
  return { zoom: next, x: anchor.x - (anchor.x - camera.x) * ratio,
    y: anchor.y - (anchor.y - camera.y) * ratio };
}

export function wheelZoom(camera, delta, mode, height, anchor) {
  if (!Number.isFinite(delta) || !Number.isFinite(height)) return camera;
  const pixels = delta * (mode === 1 ? 16 : mode === 2 ? Math.max(1, height) : 1);
  // Bound a single device event, while allowing fine touchpad deltas.
  return zoomCamera(camera, camera.zoom * Math.exp(-Math.max(-240, Math.min(240, pixels)) * .002), anchor);
}

export function dragCamera(start, dx, dy) {
  if (![dx, dy].every(Number.isFinite)) return start;
  return { ...start, x: start.x + dx, y: start.y + dy };
}

export class SceneDrag {
  begin(id, x, y, camera, surface) {
    if (this.start) return false;
    this.start={id,x,y,camera,surface,moved:false};
    return true;
  }
  move(id,x,y) {
    const start=this.start;
    if (!start || start.id!==id) return null;
    const dx=x-start.x,dy=y-start.y;
    if (!start.moved && Math.hypot(dx,dy)<4) return null;
    start.moved=true;
    return dragCamera(start.camera,dx,dy);
  }
  end(id,x,y,cancelled=false) {
    const start=this.start;
    if (!start || start.id!==id) return null;
    this.start=null;
    const moved=start.moved || Math.hypot(x-start.x,y-start.y)>=4;
    return {surface:!cancelled&&!moved ? start.surface : null};
  }
}
