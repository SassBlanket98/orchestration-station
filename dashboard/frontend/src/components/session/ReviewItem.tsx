import type { Review } from "../../api/types";
import { ModelBadge, TimeAgo } from "../ui";
import { FindingRow } from "./FindingRow";

/** One review file: its name, model and findings. */
export function ReviewItem({ review }: { review: Review }) {
  return (
    <article className="review-item">
      <header className="review-item__head">
        <span className="mono">{review.name}</span>
        <ModelBadge model={review.model} />
        <span className="review-item__time">
          <TimeAgo at={review.mtime} />
        </span>
      </header>
      {review.findings.length === 0 ? (
        <span className="faint">no findings</span>
      ) : (
        <ul className="finding-list">
          {review.findings.map((finding, i) => (
            <FindingRow key={i} finding={finding} />
          ))}
        </ul>
      )}
    </article>
  );
}
