// Lint for the trace viewer's scripts, run by pre-commit. By hand, from the repository root:
//   npx eslint@10 -c mango/util/viewer/eslint.config.mjs mango/util/viewer/js
// The config imports nothing, so it works wherever eslint itself is installed.

// the browser API the viewer uses; a new one is added here
const BROWSER = [
  "MutationObserver",
  "ResizeObserver",
  "addEventListener",
  "cancelAnimationFrame",
  "clearTimeout",
  "document",
  "getComputedStyle",
  "history",
  "innerHeight",
  "innerWidth",
  "localStorage",
  "location",
  "matchMedia",
  "performance",
  "requestAnimationFrame",
  "requestIdleCallback",
  "scrollBy",
  "scrollY",
  "setTimeout",
  "window",
];

export default [
  {
    files: ["**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: Object.fromEntries(BROWSER.map(name => [name, "readonly"])),
    },
    rules: {
      "no-undef": "error",
      "no-unused-vars": ["error", { caughtErrors: "none" }],
      "no-import-assign": "error",
      "no-const-assign": "error",
      "no-func-assign": "error",
      "no-redeclare": "error",
      "no-self-assign": "error",
      "no-dupe-keys": "error",
      "no-duplicate-case": "error",
      "no-dupe-else-if": "error",
      "no-unreachable": "error",
      "no-fallthrough": "error",
      "no-cond-assign": "error",
      "no-constant-binary-expression": "error",
      "no-unsafe-optional-chaining": "error",
      "no-useless-escape": "error",
      "no-empty": ["error", { allowEmptyCatch: true }],
      "use-isnan": "error",
      "valid-typeof": "error",
      // trace_viewer.py joins the modules into one scope; one declaration per statement lets it check the names
      "one-var": ["error", "never"],
      "no-shadow": "error",
      "prefer-const": "error",
      eqeqeq: ["error", "smart"],
    },
  },
];
