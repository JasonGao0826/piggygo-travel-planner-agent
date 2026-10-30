// localStorage 在隐私模式、被禁用或预渲染时可能不可用，所有读写都包在 try/catch 里。

export function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeStorage(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // 存不了就算了，不影响使用
  }
}

export const memberKey = (tripId: string) => `tripagent:member:${tripId}`;
export const intakeDraftKey = "tripagent:intake-draft";
