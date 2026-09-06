// Y.G.L Office System -- frontend entrypoint (Phase 8.1 scaffold).
// Admin Dashboard and Member Portal are separate route trees within this
// single app for Phase 8.1 (can be split into separate builds later if
// needed -- see Recommended Technical Stack Section 3 note).
import React from "react";
import ReactDOM from "react-dom/client";

function App() {
  return (
    <div>
      <h1>Y.G.L Office System</h1>
      <p>Phase 8.1 -- Foundation scaffold. Modules under active development.</p>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
