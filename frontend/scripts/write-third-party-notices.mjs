import { readFile, readdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const packages = ['react', 'react-dom', 'scheduler', 'motion', 'framer-motion', 'motion-dom', 'motion-utils', 'lucide-react', 'tslib'];
const notices = ['# Third-party notices\n\nRuntime packages included in the local OptiForge UI preview.\n'];
for (const name of packages) {
  const directory = path.join(frontend, 'node_modules', name);
  const metadata = JSON.parse(await readFile(path.join(directory, 'package.json'), 'utf8'));
  const files = await readdir(directory);
  const license = files.find((file) => /^license(?:\.(?:md|txt))?$/i.test(file));
  if (!license) throw new Error(`License file missing for ${name}`);
  notices.push(`## ${name} ${metadata.version}\n\n${await readFile(path.join(directory, license), 'utf8')}\n`);
}
await writeFile(path.join(frontend, 'THIRD_PARTY_NOTICES.md'), notices.join('\n'), 'utf8');
console.log(`Preserved notices for ${packages.length} runtime packages`);
