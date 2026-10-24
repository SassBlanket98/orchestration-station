import { useEffect, useRef } from "react";

const KEYS: [string, string][] = [
  ["j k  ↑ ↓", "move through the roll-up"],
  ["Enter", "open a stage or worker, or toggle a fold"],
  ["→  ←", "open or close the selected fold"],
  ["e", "expand or collapse all projects"],
  ["h", "show or hide idle projects"],
  ["a", "show or hide closed projects"],
  ["g  G", "first  ·  last"],
  ["/", "filter stages by name, label, project, model or worker"],
  ["Esc", "clear the filter"],
  ["?", "this help"],
];

// Mounted only while open. Focus moves in on open and returns to the previous element on close.
export function BoardHelp({ onClose }: { onClose: () => void }) {
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    close.current?.focus();
    return () => previous?.focus();
  }, []);
  return <div className="plant-help-backdrop" onMouseDown={onClose}>
    <div role="dialog" aria-modal="true" aria-label="Keyboard shortcuts" className="plant-help" onMouseDown={(event) => event.stopPropagation()}>
      <h2>Keys</h2>
      <dl>{KEYS.map(([keys, what]) => <div key={keys}><dt><kbd>{keys}</kbd></dt><dd>{what}</dd></div>)}</dl>
      <button ref={close} type="button" className="plant-help-close" onClick={onClose}>close <kbd>?</kbd></button>
    </div>
  </div>;
}
