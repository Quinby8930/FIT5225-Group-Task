export function canOpenPreview(item) {
  return item?.legacy !== true
    && item?.can_preview === true
    && (item.file_type === "image" || item.file_type === "video");
}

export function canRenderInlinePreview(item) {
  return canOpenPreview(item);
}

export function canOpenFullImage(item) {
  return canOpenPreview(item)
    && item.file_type === "image"
    && typeof item.original_key === "string"
    && item.original_key.length > 0;
}

export function openDetachedWindow(openWindow) {
  if (typeof openWindow !== "function") return null;
  const popup = openWindow("about:blank", "_blank");
  if (!popup) return null;
  try {
    popup.opener = null;
  } catch {
    popup.close?.();
    return null;
  }
  return popup;
}

export async function withDetachedWindow(openWindow, work) {
  const popup = openDetachedWindow(openWindow);
  if (!popup) return { opened: false, value: undefined };
  return { opened: true, value: await work(popup) };
}
