import "@fontsource-variable/inter";
import "@fontsource-variable/playfair-display";
import "@fontsource-variable/playfair-display/wght-italic.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
