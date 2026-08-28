export default function StatusBanner({ status }) {
  if (!status?.message) return null;
  const isError = status.type === "error";
  return <p className={`status ${status.type || "info"}`} role={isError ? "alert" : "status"} aria-live={isError ? undefined : "polite"} aria-atomic="true">{status.message}</p>;
}
