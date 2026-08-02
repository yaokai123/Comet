import { TeamOutlined } from '@ant-design/icons'
import { AuthenticatedImage } from '@/components/AuthenticatedImage'

const COLORS = [
  '#155EEF',
  '#7C4DFF',
  '#0E9F6E',
  '#F05252',
  '#FF8A4C',
  '#0694A2',
  '#EB2F96',
]
function colorFor(name: string): string {
  let h = 0
  for (let i = 0; i < name.length; i += 1) h = (h * 31 + name.charCodeAt(i)) % 997
  return COLORS[h % COLORS.length]
}

interface Member {
  name: string
  avatar_url?: string | null
}

/** 角色卡组宫格合成头像（仿微信群）：前 1~4 个成员拼方块，无头像用名字首字色块。 */
export default function GroupComboAvatar({
  members,
  size = 48,
}: {
  members: Member[]
  size?: number
}) {
  const list = members.slice(0, 4)
  if (list.length === 0) {
    return (
      <div
        className="gc-group-avatar gc-group-avatar--empty"
        style={{ width: size, height: size }}
      >
        <TeamOutlined />
      </div>
    )
  }
  if (list.length === 1) {
    const m = list[0]
    return (
      <div
        className="gc-group-avatar"
        style={{ width: size, height: size, display: 'block' }}
      >
        <div className="gc-group-avatar-cell">
          {m.avatar_url ? (
            <AuthenticatedImage
              src={m.avatar_url}
              alt={m.name}
              style={{ width: '100%', height: '100%', objectFit: 'cover' }}
            />
          ) : (
            <span
              className="gc-group-avatar-letter"
              style={{ background: colorFor(m.name) }}
            >
              {m.name.slice(0, 1)}
            </span>
          )}
        </div>
      </div>
    )
  }
  return (
    <div
      className={`gc-group-avatar gc-group-avatar--${list.length}`}
      style={{ width: size, height: size }}
    >
      {list.map((m, i) => (
        <div className="gc-group-avatar-cell" key={i}>
          {m.avatar_url ? (
            <AuthenticatedImage
              src={m.avatar_url}
              alt={m.name}
              style={{ width: '100%', height: '100%', objectFit: 'cover' }}
            />
          ) : (
            <span
              className="gc-group-avatar-letter"
              style={{ background: colorFor(m.name) }}
            >
              {m.name.slice(0, 1)}
            </span>
          )}
        </div>
      ))}
    </div>
  )
}
