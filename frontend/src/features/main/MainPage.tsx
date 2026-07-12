import { useNavigate } from "react-router-dom";

import { Button } from "../../components/Button";
import { ServersSection } from "../dashboard/ServersSection";
import { ServicesSection } from "../services/ServicesSection";
import classes from "./main.module.css";

export function MainPage() {
  const navigate = useNavigate();

  return (
    <main className={classes.page}>
      <section className={classes.section}>
        <header className={classes.sectionHeader}>
          <div>
            <h2 className={classes.sectionTitle}>Servers</h2>
            <p className={classes.sectionSub}>등록된 로컬 서버를 한 곳에서 관리</p>
          </div>
          <div className={classes.sectionActions}>
            <Button variant="ghost" onClick={() => navigate("/servers")}>
              모두 보기
            </Button>
            <Button variant="primary" onClick={() => navigate("/services/new")}>
              서버 등록
            </Button>
          </div>
        </header>
        <ServersSection showToolbar={false} />
      </section>

      <section className={classes.section}>
        <ServicesSection compact />
      </section>
    </main>
  );
}
