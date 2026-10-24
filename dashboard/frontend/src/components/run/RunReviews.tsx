import type { Review } from "../../api/types";

export function RunReviews({ reviews }: { reviews: Review[] }) {
  const findings = reviews.flatMap((review) => review.findings.map((finding) => ({ review, finding })));
  return <section>
    <div className="run-section-head"><div><h2>Reviews</h2><p>Findings are shown with their source review; ownership and resolution are not present in the feed.</p></div><span className="run-source">{reviews.length ? `${findings.length} findings` : "NO SOURCE"}</span></div>
    <div className="run-review-list panel">
      {reviews.length ? reviews.map((review) => <div className="run-review" key={review.name}>
        <span className="run-priority">{review.findings.length ? review.findings.map((f) => f.priority).join(" · ") : "—"}</span>
        <div><h3>{review.name}</h3><p>{review.model ?? "?"} · {new Date(review.mtime * 1000).toLocaleString()}</p>
          {review.findings.length ? review.findings.map((finding, index) => <div className="run-finding" key={`${finding.priority}-${index}`}><b>{finding.priority}</b><span>{finding.text}</span><small>Owner ? · resolution ?</small></div>) : <div className="run-finding quiet">No findings recorded in this review.</div>}
        </div><span className="run-source">NO SOURCE · owner / resolution</span>
      </div>) : <div className="run-empty">NO SOURCE · no review files were supplied for this run.</div>}
    </div>
    {reviews.length > 0 && <p className="run-review-note">Finding ownership and resolution were not supplied, so both remain unknown.</p>}
  </section>;
}
