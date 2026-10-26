import { readFileSync, readdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import type { Plugin } from "vite";

/** Ship the CMaps from the installed PDF.js version, without a remote CDN. */
export function pdfCMaps(): Plugin {
  const require = createRequire(import.meta.url);
  const folder = join(dirname(require.resolve("pdfjs-dist/package.json")), "cmaps");
  const files = readdirSync(folder).filter(name => name.endsWith(".bcmap") || name === "LICENSE");
  let base = "/";
  return {
    name: "pdfjs-cmaps",
    configResolved(config) { base = config.base; },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const path = (req.url ?? "").split("?")[0];
        const prefix = `${base}pdfjs/cmaps/`;
        if (!path.startsWith(prefix)) return next();
        const name = path.slice(prefix.length);
        if (!files.includes(name)) { res.statusCode = 404; res.end(); return; }
        res.setHeader("Content-Type", name === "LICENSE" ? "text/plain" : "application/octet-stream");
        res.end(readFileSync(join(folder, name)));
      });
    },
    generateBundle() {
      for (const name of files) {
        this.emitFile({ type: "asset", fileName: `pdfjs/cmaps/${name}`, source: readFileSync(join(folder, name)) });
      }
    },
  };
}
