// Export the prototype's hand-tuned goal moments to data_gen/proto_moments.json (consumed by data_gen/proto_demo.py).
// Usage: node scripts/export_proto_moments.mjs [path/to/good-assist-proto/src/data.js]
import { pathToFileURL, fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';
import { writeFileSync } from 'node:fs';

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(process.argv[2] || resolve(here, '../../good-assist-proto/src/data.js'));
const { MOMENTS, EVENTS } = await import(pathToFileURL(src).href);
const out = resolve(here, '../data_gen/proto_moments.json');
writeFileSync(out, JSON.stringify({ moments: MOMENTS, events: EVENTS }, null, 1) + '\n');
console.log(`wrote ${out}: ${MOMENTS.length} moments, ${EVENTS.length} events`);
