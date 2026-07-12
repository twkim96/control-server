export type DropPosition = "before" | "after";

export function getVerticalDropPosition(
  clientY: number,
  rect: Pick<DOMRect, "top" | "height">,
): DropPosition {
  return clientY < rect.top + rect.height / 2 ? "before" : "after";
}

export function moveById(
  ids: string[],
  sourceId: string,
  targetId: string,
  position: DropPosition,
): string[] {
  const sourceIndex = ids.indexOf(sourceId);
  const targetIndex = ids.indexOf(targetId);
  if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return ids;
  return moveByIndex(ids, sourceIndex, targetIndex, position);
}

export function moveByIndex<T>(
  items: T[],
  sourceIndex: number,
  targetIndex: number,
  position: DropPosition,
): T[] {
  if (
    sourceIndex < 0 ||
    targetIndex < 0 ||
    sourceIndex >= items.length ||
    targetIndex >= items.length ||
    sourceIndex === targetIndex
  ) {
    return items;
  }

  const next = [...items];
  const [moved] = next.splice(sourceIndex, 1);
  let insertIndex = targetIndex;
  if (sourceIndex < targetIndex) insertIndex -= 1;
  if (position === "after") insertIndex += 1;
  next.splice(Math.max(0, Math.min(insertIndex, next.length)), 0, moved);
  return next;
}

export function orderById<T extends { id: string }>(items: T[], order?: string[]): T[] {
  if (!order) return items;
  const byId = new Map(items.map((item) => [item.id, item]));
  const ordered = order.flatMap((id) => {
    const item = byId.get(id);
    return item ? [item] : [];
  });
  if (ordered.length !== items.length) return items;
  return ordered;
}
