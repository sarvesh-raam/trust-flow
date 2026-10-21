/* ESLint config for the Vite + React + TypeScript app (eslint 8, legacy config). */
module.exports = {
  root: true,
  env: { browser: true, es2020: true },
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
    "plugin:react-hooks/recommended",
  ],
  ignorePatterns: ["dist", ".eslintrc.cjs", "*.py"],
  parser: "@typescript-eslint/parser",
  plugins: ["react-refresh"],
  rules: {
    // Context hooks and shadcn variant helpers live next to their components.
    "react-refresh/only-export-components": [
      "warn",
      { allowConstantExport: true, allowExportNames: ["useAuth", "useDemoMode", "badgeVariants", "buttonVariants"] },
    ],
    // `_name` marks a parameter kept for its signature (e.g. a context default).
    "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
  },
};
