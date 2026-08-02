// LRC 歌词解析：把带时间戳的 LRC 文本解析为按时间排序的行数组。

export interface LrcLine {
  time: number // 秒
  text: string
}

// 匹配一行里的所有时间标签 [mm:ss.xx] / [mm:ss]
const TAG_RE = /\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g

export function parseLrc(raw: string | null | undefined): LrcLine[] {
  if (!raw) return []
  const lines: LrcLine[] = []
  for (const rawLine of raw.split(/\r?\n/)) {
    const text = rawLine.replace(TAG_RE, '').trim()
    TAG_RE.lastIndex = 0
    let m: RegExpExecArray | null
    const tags: number[] = []
    while ((m = TAG_RE.exec(rawLine)) !== null) {
      const min = Number(m[1])
      const sec = Number(m[2])
      const fracRaw = m[3] ?? '0'
      // 毫秒/厘秒归一到秒的小数
      const frac = Number(fracRaw) / 10 ** fracRaw.length
      tags.push(min * 60 + sec + frac)
    }
    if (tags.length === 0) continue
    if (!text) continue // 跳过纯标签元信息行（如 [ti:]、空行）
    for (const t of tags) {
      lines.push({ time: t, text })
    }
  }
  lines.sort((a, b) => a.time - b.time)
  return lines
}

// 根据当前播放时间，返回当前应高亮的行索引（-1 表示还没到第一行）
export function activeLineIndex(lines: LrcLine[], current: number): number {
  if (lines.length === 0) return -1
  let idx = -1
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].time <= current) idx = i
    else break
  }
  return idx
}
