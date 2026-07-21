export default [
  {
    ignores: ["dist/**"]
  },
  {
    files: ["src/**/*.js", "tests/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: {
        document: "readonly",
        fetch: "readonly",
        window: "readonly"
      }
    },
    rules: {
      "no-undef": "error",
      "no-unused-vars": "error"
    }
  }
];
