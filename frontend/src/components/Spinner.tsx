import classes from "./components.module.css";

export function Spinner() {
  return <span className={classes.spinner} aria-label="loading" />;
}
