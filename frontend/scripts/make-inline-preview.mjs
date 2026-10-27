import { readFile, readdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const assetDir = path.join(frontend, 'dist', 'assets');
const files = await readdir(assetDir);
const css = await readFile(path.join(assetDir, files.find((file) => file.endsWith('.css'))), 'utf8');
const js = await readFile(path.join(assetDir, files.find((file) => file.endsWith('.js'))), 'utf8');
const notices = await readFile(path.join(frontend, 'THIRD_PARTY_NOTICES.md'), 'utf8');
const target = path.resolve(frontend, '..', 'design-preview', 'optiforge-soft-studio.html');
const fragment = `<div id="optiforge-soft-studio" data-inline-preview></div>\n<style>\n${css}\n</style>\n<script type="module">\n${js.replaceAll('</script', '<\\/script')}\n</script>\n<!--\n${notices.replaceAll('-->', '-- >')}\n-->\n`;
if (Buffer.byteLength(fragment) > 1_000_000) throw new Error('Preview exceeds the inline size limit');
await writeFile(target, fragment, 'utf8');
console.log(`Built inline preview: ${Buffer.byteLength(fragment)} bytes`);
