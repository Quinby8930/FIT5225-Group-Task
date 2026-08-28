import { Children, cloneElement, isValidElement, useId } from "react";

export default function Field({ label, children }) {
  const id = useId();
  const control = Children.map(children, (child) =>
    isValidElement(child) ? cloneElement(child, { id }) : child
  );
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {control}
    </div>
  );
}
