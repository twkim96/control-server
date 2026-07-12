import { Navigate, createBrowserRouter } from "react-router-dom";

import { App } from "./App";
import { MainPage } from "./features/main/MainPage";
import { ServersPage } from "./features/servers/ServersPage";
import { ServicesPage } from "./features/services/ServicesPage";
import { SettingsPage } from "./features/settings/SettingsPage";
import { ServiceFormPage } from "./features/service-form/ServiceFormPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <MainPage /> },
      { path: "servers", element: <ServersPage /> },
      { path: "services", element: <ServicesPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "services/new", element: <ServiceFormPage mode="create" /> },
      { path: "services/:id/edit", element: <ServiceFormPage mode="edit" /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);
