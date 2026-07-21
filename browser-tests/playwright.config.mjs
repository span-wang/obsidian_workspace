import { defineConfig } from "@playwright/test";

const testPort = Number(process.env.OBSIDIAN_PLATFORM_TEST_PORT || "6240");

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.mjs",
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: `http://127.0.0.1:${testPort}`
  }
});
