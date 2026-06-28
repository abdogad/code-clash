import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// Self-hosted fonts (bundled into the build — no runtime CDN dependency).
import "@fontsource-variable/hanken-grotesk";
import "@fontsource-variable/jetbrains-mono";

// tokens first (the :root contract), then base/keyframes.
import "./styles/tokens.css";
import "./styles/global.css";

import { DuelProvider } from "./state/DuelContext";
import { App } from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <DuelProvider>
      <App />
    </DuelProvider>
  </StrictMode>,
);
