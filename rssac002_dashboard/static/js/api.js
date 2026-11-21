// Calls to the dashboard's own API. Errors carry the server's explanation.

const cache = new Map();

async function request(path, options = {}) {
  const init = { method: options.method || "GET", headers: {}, credentials: "same-origin" };
  if (init.method !== "GET") init.headers["X-Dashboard-Request"] = "1";
  if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    throw new Error("The dashboard server did not answer. Check that it is still running.");
  }
  if (options.text) {
    if (!response.ok) throw new Error(`The server answered ${response.status}.`);
    return response.text();
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((item) => item.msg).join("; ") : data.detail;
    const error = new Error(detail || `The server answered ${response.status}.`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function query(params) {
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(name, value);
  }
  return search.toString();
}

/** GET with a small in-memory cache, so switching pages or toggles does not refetch. */
function cached(path) {
  if (!cache.has(path)) {
    if (cache.size > 60) cache.delete(cache.keys().next().value);
    cache.set(path, request(path).catch((error) => {
      cache.delete(path);
      throw error;
    }));
  }
  return cache.get(path);
}

export const api = {
  clearCache: () => cache.clear(),
  meta: () => request("/api/meta"),
  series: (params) => cached(`/api/series?${query(params)}`),
  sizes: (params) => cached(`/api/sizes?${query(params)}`),
  csvUrl: (params) => `/api/series.csv?${query(params)}`,
  importers: () => request("/api/importers"),
  runImporter: (key, importFrom) => request(`/api/importers/${key}`, { method: "POST", body: { import_from: importFrom || null } }),
  quality: (params) => cached(`/api/quality?${query(params)}`),
  events: (source) => request(`/api/events?${query({ source })}`),
  addEvent: (body) => request("/api/events", { method: "POST", body }),
  removeEvent: (id) => request(`/api/events/${id}`, { method: "DELETE" }),
  coverage: (source, server) => cached(`/api/coverage?${query({ source, server })}`),
  reports: (params) => cached(`/api/reports?${query(params)}`),
  reportText: (id) => request(`/api/reports/${id}`, { text: true }),
  archiveUrl: (params) => `/api/archive?${query(params)}`,
  signIn: (password) => request("/api/session", { method: "POST", body: { password } }),
  signOut: () => request("/api/session", { method: "DELETE" }),
  previewSource: (body) => request("/api/sources/preview", { method: "POST", body }),
  addSource: (body) => request("/api/sources", { method: "POST", body }),
  removeSource: (id) => request(`/api/sources/${id}`, { method: "DELETE" }),
  scans: (id) => request(`/api/sources/${id}/scans`),
  startScan: (id, mode) => request(`/api/sources/${id}/scan`, { method: "POST", body: { mode } }),
};
