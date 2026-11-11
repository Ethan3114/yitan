import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  outputDir: "../.intermediate/book-tests",
  workers: 1,
  reporter: "line",
  webServer: {
    command: "node server.mjs",
    env: { PORT: "3012", NODE_ENV: "production" },
    url: "http://127.0.0.1:3012/books",
    reuseExistingServer: false,
  },
});
