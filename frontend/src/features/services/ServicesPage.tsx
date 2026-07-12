import { ServicesSection } from "./ServicesSection";
import classes from "./services.module.css";

export function ServicesPage() {
  return (
    <main className={classes.page}>
      <ServicesSection />
    </main>
  );
}
