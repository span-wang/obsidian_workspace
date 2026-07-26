import { fileURLToPath } from "node:url";

import { defineConfig } from "vite";

const labRoot = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  root: labRoot,
  base: "/_test/retrieval-chunking/",
  build: {
    outDir: fileURLToPath(new URL("./dist", import.meta.url)),
    emptyOutDir: true
  }
});
