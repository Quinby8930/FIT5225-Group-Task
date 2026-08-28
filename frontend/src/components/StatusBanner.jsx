export default function StatusBanner({ status }) {
  if (!status?.message) return null;
  return <p className={`status ${status.type || "info"}`} role="status">{status.message}</p>;
}
