export function formatDate(
  value: string | null | undefined,
  options?: Intl.DateTimeFormatOptions
) {
  if (!value) return "Not recorded"
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return "Not recorded"
  return new Intl.DateTimeFormat("en-MY", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Kuala_Lumpur",
    ...options,
  }).format(date)
}

export function formatBytes(value: number) {
  if (!Number.isFinite(value) || value < 0) return "Unknown size"
  if (value < 1024) return `${value} B`
  const units = ["KB", "MB", "GB"]
  let amount = value / 1024
  let unit = units[0]
  for (let index = 1; index < units.length && amount >= 1024; index += 1) {
    amount /= 1024
    unit = units[index]
  }
  return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${unit}`
}

export function titleCase(value: string) {
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase())
}

export function shortHash(value: string, length = 16) {
  return value.length > length ? `${value.slice(0, length)}…` : value
}

export function safeExternalUrl(value: string) {
  try {
    const url = new URL(value)
    return url.protocol === "https:" || url.protocol === "http:"
      ? url.toString()
      : null
  } catch {
    return null
  }
}

export function withSearchPage(parameters: URLSearchParams, page: number) {
  const next = new URLSearchParams(parameters)
  next.set("page", String(page))
  return `?${next.toString()}`
}
