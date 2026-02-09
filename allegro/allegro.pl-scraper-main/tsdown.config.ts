import { defineConfig } from 'tsdown';

export default defineConfig({
    entry: ['src/index.tsx', 'src/executor.ts'],
    format: 'esm',
    platform: 'node',
    target: 'node22',
    outDir: 'dist',
    clean: true,
    inlineOnly: false,
    noExternal: [/^ink/, /^react/, /^yoga-layout/, /^chalk/],
    external: [
        '@hono/node-server',
        'hono',
        'cheerio',
        'dotenv',
        'nanoid',
        'set-cookie-parser',
        'tlsclientwrapper',
        'undici',
        'async-mutex',
        'react-devtools-core',
    ],
});
