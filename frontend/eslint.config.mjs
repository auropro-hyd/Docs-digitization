import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { FlatCompat } from "@eslint/eslintrc";

// ``eslint-config-next`` ships legacy ``.eslintrc``-style configs
// (``module.exports = { extends: [...] }``); ESLint 9 needs the
// FlatCompat shim to consume them from a flat config. This is the
// pattern Next's own ``create-next-app`` template emits — see the
// upstream docs at https://nextjs.org/docs/app/api-reference/config/eslint.
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({ baseDirectory: __dirname });

const eslintConfig = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    rules: {
      // Honour the ``_``-prefix convention for intentionally-unused
      // params and bindings. Several files in the codebase already use
      // this convention (e.g. ``_node``, ``_actor``); without these
      // patterns the ``no-unused-vars`` rule fires false positives
      // and drowns out real findings.
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
          destructuredArrayIgnorePattern: "^_",
        },
      ],
    },
  },
  {
    ignores: [".next/**", "out/**", "build/**", "next-env.d.ts"],
  },
];

export default eslintConfig;
