export const routeTable = {
  overview: "/",
  quota: "/quota",
  logs: "/logs",
  run: "/runs/:sid",
  timeline: "/runs/:sid/timeline",
  workers: "/runs/:sid/workers",
  reviews: "/runs/:sid/reviews",
  worker: "/runs/:sid/workers/:name",
} as const;

export const routePaths = Object.values(routeTable);
