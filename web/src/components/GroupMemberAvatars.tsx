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
  id?: string
  name: string
  avatar_url?: string | null
}

/** 卡组成员头像：一排叠放的完整圆形头像（最多 5 个 +N），每个都是完整方形/圆形不裁脸。 */
export default function GroupMemberAvatars({
  members,
  size = 40,
  max = 5,
}: {
  members: Member[]
  size?: number
  max?: number
}) {
  const shown = members.slice(0, max)
  const rest = members.length - shown.length
  return (
    <div className="gma-row">
      {shown.map((m, i) => (
        <div
          key={m.id || i}
          className="gma-item"
          style={{ width: size, height: size }}
          title={m.name}
        >
          {m.avatar_url ? (
            <AuthenticatedImage
              src={m.avatar_url}
              alt={m.name}
              style={{ width: '100%', height: '100%', objectFit: 'cover' }}
            />
          ) : (
            <span className="gma-letter" style={{ background: colorFor(m.name) }}>
              {m.name.slice(0, 1)}
            </span>
          )}
        </div>
      ))}
      {rest > 0 && (
        <div className="gma-item gma-more" style={{ width: size, height: size }}>
          +{rest}
        </div>
      )}
    </div>
  )
}
