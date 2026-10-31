// Small fetch helper used by the Change Impact Analyzer.
// It mirrors the helper in App.jsx (same base URL and error messages).

export const API_URL =
  import.meta.env.VITE_API_URL || "http://localhost:8001";

export async function apiRequest(path, options = {}) {
  let response;

  try {
    response = await fetch(`${API_URL}${path}`, options);
  } catch {
    throw new Error(
      "Cannot connect to the backend. Make sure FastAPI is running on port 8001."
    );
  }

  const text = await response.text();
  let data;

  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error("The server returned an invalid response.");
  }

  if (!response.ok) {
    const detail = data.detail;

    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
        ? detail.map((item) => item.msg).join(", ")
        : `Request failed (${response.status})`;

    throw new Error(message);
  }

  return data;
}
