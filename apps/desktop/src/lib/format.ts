export function formatBytes(
  bytes: number,
): string {
  if (!Number.isFinite(bytes)) {
    return "";
  }

  if (bytes < 1024) {
    return `${bytes} B`;
  }

  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }

  return `${(
    bytes /
    (1024 * 1024)
  ).toFixed(1)} MB`;
}

export function relativeTime(
  iso: string,
): string {
  const date = new Date(iso);
  const diff =
    Date.now() - date.getTime();

  const minute = 60_000;
  const hour = minute * 60;
  const day = hour * 24;

  if (diff < minute) {
    return "now";
  }

  if (diff < hour) {
    return `${Math.floor(
      diff / minute,
    )}m`;
  }

  if (diff < day) {
    return `${Math.floor(
      diff / hour,
    )}h`;
  }

  if (diff < day * 7) {
    return `${Math.floor(
      diff / day,
    )}d`;
  }

  return date.toLocaleDateString();
}

export function basenameModel(
  model: string,
): string {
  const pieces = model.split("/");

  return pieces[
    pieces.length - 1
  ] || model;
}

/** A 0..1 rate as a whole percentage: `0.872` → `"87%"`. */
export function percent(
  rate: number,
): string {
  if (!Number.isFinite(rate)) {
    return "0%";
  }

  return `${Math.round(rate * 100)}%`;
}