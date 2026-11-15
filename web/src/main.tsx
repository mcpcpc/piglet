import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { connectionParams } from "./protocol/client";
import { connect } from "./state/store";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

const params = connectionParams();
if (params) connect(params.url, params.token);
else showMissingToken(root);

function showMissingToken(element: HTMLElement) {
  element.innerHTML =
    '<div style="font:14px system-ui;max-width:480px;margin:20vh auto;padding:0 1rem">' +
    '<h1 style="font-size:16px">piglet</h1><p>This page needs the access link printed by the <code>piglet</code> ' +
    "command. Run <code>piglet</code> again and open the URL it shows.</p></div>";
}

if (params) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
