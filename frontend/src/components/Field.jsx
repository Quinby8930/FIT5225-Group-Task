export default function Field({ label, children }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}
