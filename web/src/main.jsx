import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import AdminVideo from "./AdminVideo.jsx";
import "./styles.css";

const path = window.location.pathname.replace(/\/+$/, "") || "/";
const root = createRoot(document.getElementById("root"));
root.render(path === "/admin/video" ? <AdminVideo /> : <App />);
