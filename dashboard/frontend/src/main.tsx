import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, MemoryRouter } from "react-router-dom";
import App from "./App";
import { PhoneNav } from "./components/phone/PhoneNav";
import "./styles/tokens.css";
import "./styles/global.css";
import "./styles/components.css";
import "./styles/phone.css";
import { registerServiceWorker } from "./pwa/register";

const container = document.getElementById("root");
if (container === null) throw new Error("#root element is missing");

const app = (
  <>
    <App />
    <PhoneNav />
  </>
);

// The artifact build runs inside an iframe with no server, so it cannot use the URL.
const artifact = import.meta.env.VITE_ARTIFACT === "1";
// Production only (checked inside), and never in the artifact build.
if (!artifact) registerServiceWorker();

createRoot(container).render(
  <StrictMode>
    {artifact ? (
      <MemoryRouter initialEntries={["/?mock=1"]}>{app}</MemoryRouter>
    ) : (
      <BrowserRouter>{app}</BrowserRouter>
    )}
  </StrictMode>,
);
