import { Button, Empty, Result, Spin } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import type { ReactNode } from 'react'

export type WorkspaceStateKind = 'loading' | 'empty' | 'error' | 'disabled'

type WorkspaceStateProps = {
  kind: WorkspaceStateKind
  title?: string
  description?: ReactNode
  action?: ReactNode
  onRetry?: () => void
  className?: string
}

export default function WorkspaceState({
  kind,
  title,
  description,
  action,
  onRetry,
  className,
}: WorkspaceStateProps) {
  const rootClassName = `ui-workspace-state ui-workspace-state--${kind}${className ? ` ${className}` : ''}`

  if (kind === 'loading') {
    return (
      <div className={rootClassName} aria-busy="true" aria-live="polite">
        <Spin size="large" />
        <strong>{title || '正在加载'}</strong>
        {description && <span>{description}</span>}
      </div>
    )
  }

  if (kind === 'error') {
    return (
      <Result
        className={rootClassName}
        status="error"
        title={title || '内容暂时无法加载'}
        subTitle={description}
        extra={onRetry ? <Button icon={<ReloadOutlined />} onClick={onRetry}>重试</Button> : action}
      />
    )
  }

  if (kind === 'disabled') {
    return (
      <Result
        className={rootClassName}
        status="warning"
        title={title || '此功能暂不可用'}
        subTitle={description}
        extra={action}
      />
    )
  }

  return (
    <div className={rootClassName}>
      <Empty description={title || '暂无内容'} />
      {(description || action) && <div className="ui-workspace-state__footer">{description}{action}</div>}
    </div>
  )
}
