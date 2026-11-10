/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: "#f6f1e8",
        ink: "#1c1916",
        clay: "#8c4a32",
      },
    },
  },
  plugins: [],
};
