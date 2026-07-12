import { useNavigate } from "react-router-dom";

import { Button } from "../../components/Button";
import { ServersSection } from "../dashboard/ServersSection";
import classes from "./servers.module.css";

export function ServersPage() {
  const navigate = useNavigate();
  return (
    <main className={classes.page}>
      <header className={classes.header}>
        <div>
          <h1 className={classes.title}>Servers</h1>
          <p className={classes.subtitle}>등록된 로컬 서버를 모두 보고 제어</p>
        </div>
        <div className={classes.actions}>
          <Button variant="primary" onClick={() => navigate("/services/new")}>
            서버 등록
          </Button>
        </div>
      </header>
      <ServersSection showToolbar />
    </main>
  );
}
