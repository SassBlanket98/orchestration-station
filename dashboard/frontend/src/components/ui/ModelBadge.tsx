/** The model name for a worker, or a dashed placeholder when unknown. */
export function ModelBadge({ model }: { model: string | null }) {
  if (model === null || model === "") {
    return <span className="model-badge model-badge--empty">no model</span>;
  }
  return <span className="model-badge">{model}</span>;
}
