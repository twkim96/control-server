import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";

import "@sun-typeface/suit/fonts/variable/woff2/SUIT-Variable.css";
import "./styles/global.css";
import {
  applyAppearanceSettings,
  readAppearanceSettings,
  syncAppearanceSettingsFromServer,
} from "./features/settings/appearance";
import { router } from "./router";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("#root 요소를 찾을 수 없습니다.");
}

applyAppearanceSettings(readAppearanceSettings());
void syncAppearanceSettingsFromServer().catch(() => {
  // 서버에 아직 닿지 못하면 local fallback을 그대로 유지한다.
});

createRoot(rootElement).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
