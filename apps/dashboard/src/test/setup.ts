import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// isolate:false in vite.config shares the worker — ensure DOM resets between tests.
afterEach(() => {
  cleanup();
});
