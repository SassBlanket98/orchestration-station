import type { Review } from "../../api/types";
import { Panel } from "../ui";
import { ReviewItem } from "./ReviewItem";

/** Review files, newest first, each with its findings. */
export function ReviewsPanel({ reviews }: { reviews: Review[] }) {
  return (
    <Panel title="reviews">
      {reviews.length === 0 ? (
        <span className="faint">no reviews yet</span>
      ) : (
        <div className="stack">
          {reviews.map((review) => (
            <ReviewItem key={review.name} review={review} />
          ))}
        </div>
      )}
    </Panel>
  );
}
