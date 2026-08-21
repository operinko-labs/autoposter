import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./theme.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <p>Autoposter</p>
  </StrictMode>,
);
