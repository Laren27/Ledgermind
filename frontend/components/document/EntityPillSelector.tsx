interface EntityPillSelectorProps {
  entities: string[];
  selected: string[];
  onToggle: (entity: string) => void;
}

export function EntityPillSelector({ entities, selected, onToggle }: EntityPillSelectorProps) {
  return (
    <div className="flex flex-wrap items-center gap-2.5">
      {entities.map((entity) => {
        const isSelected = selected.includes(entity);
        const isDisabled = !isSelected && selected.length >= 2;
        return (
          <button
            key={entity}
            type="button"
            disabled={isDisabled}
            onClick={() => onToggle(entity)}
            className="px-3.5 py-1.5 rounded-sm text-[14.5px] font-normal transition-all duration-150 border select-none"
            style={{
              fontFamily: "var(--font-ui, sans-serif)",
              color: isSelected
                ? "var(--paper-verified, #3F7A52)"
                : isDisabled
                ? "var(--ink-passive, #B7AEA3)"
                : "var(--ink-secondary, #5F574D)",
              backgroundColor: isSelected ? "rgba(63, 122, 82, 0.10)" : "rgba(223, 212, 196, 0.35)",
              borderColor: isSelected ? "var(--paper-verified, #3F7A52)" : "rgba(216, 206, 193, 0.8)",
              boxShadow: "0 1px 2px rgba(42, 36, 30, 0.04)",
              cursor: isDisabled ? "not-allowed" : "pointer",
            }}
          >
            {isSelected && "✓ "}
            {entity}
          </button>
        );
      })}
    </div>
  );
}
