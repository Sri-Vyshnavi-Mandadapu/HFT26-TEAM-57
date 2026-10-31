// Display metadata for the node types in the impact graph.

export const KIND_META = {
  target: { label: "Selected function", color: "#9c8bff", column: 2 },
  caller: { label: "Direct caller", color: "#4f93ff", column: 1 },
  indirect: { label: "Indirect caller", color: "#6f86b3", column: 0 },
  dependency: { label: "Dependency", color: "#4ec9b0", column: 3 },
  test: { label: "Related test", color: "#e5b567", column: 1 },
  indirect_test: { label: "Indirect test", color: "#a98f5a", column: 0 },
};
