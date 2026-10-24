import ReactMarkdown from "react-markdown";

/** Read-only Markdown body (briefs and reports). */
export function Markdown({ source }: { source: string | null }) {
  if (source === null || source.trim() === "") {
    return <div className="markdown faint">nothing to show</div>;
  }
  return (
    <div className="markdown">
      <ReactMarkdown>{source}</ReactMarkdown>
    </div>
  );
}
