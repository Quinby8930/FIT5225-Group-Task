export function beginQuery(previous = {}) {
  return {
    generation: (Number.isInteger(previous.generation) ? previous.generation : 0) + 1,
    phase: "loading",
    result: null,
  };
}

export function settleQuery(current, generation, result, phase) {
  if (!current || current.generation !== generation) return current;
  return { generation, phase, result };
}

export function shouldShowResultsHeader(phase) {
  return phase !== "idle";
}
