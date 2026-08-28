export default function QueryChips({ descriptor, onClear }) {
  if (!descriptor?.chips?.length) return null;
  return (
    <span className="query-chips" role="group" aria-label="Query conditions">
      {descriptor.chips.map((chip, index) => (
        <span key={`${chip.label}-${index}`} className="chip">
          <b>{chip.label}</b>
          <span>{chip.value}</span>
        </span>
      ))}
      {typeof onClear === "function" && (
        <button
          type="button"
          className="chip-clear"
          onClick={onClear}
          aria-label="Clear query and results"
        >
          ×
        </button>
      )}
    </span>
  );
}
