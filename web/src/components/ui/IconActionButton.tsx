import { Button, Tooltip } from 'antd'
import type { ButtonProps } from 'antd'
import type { ReactNode } from 'react'

type IconActionButtonProps = Omit<ButtonProps, 'children' | 'icon' | 'aria-label'> & {
  label: string
  icon: ReactNode
}

export default function IconActionButton({ label, icon, ...props }: IconActionButtonProps) {
  return (
    <Tooltip title={label}>
      <Button aria-label={label} className="ui-icon-action" icon={icon} {...props} />
    </Tooltip>
  )
}
