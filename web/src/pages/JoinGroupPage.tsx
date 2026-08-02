import { useEffect, useRef, useState } from 'react'
import { Button, Result, Spin } from 'antd'
import { useNavigate, useParams } from 'react-router-dom'
import { groupApi } from '@/api/chat'

// 凭邀请链接加入群聊：/groups/join/:code
// 未登录会跳登录页（带 redirect），登录后回来自动加入并跳进群聊。
export default function JoinGroupPage() {
  const { code } = useParams<{ code: string }>()
  const navigate = useNavigate()
  const [status, setStatus] = useState<'joining' | 'error'>('joining')
  const [errMsg, setErrMsg] = useState('')
  const done = useRef(false)

  useEffect(() => {
    if (done.current) return
    done.current = true
    if (!code) {
      setErrMsg('邀请链接无效')
      setStatus('error')
      return
    }
    // 未登录：记下目标，去登录页
    if (!localStorage.getItem('access_token')) {
      navigate(`/login?redirect=/groups/join/${code}`, { replace: true })
      return
    }
    groupApi
      .join(code)
      .then((r) => {
        navigate(`/group-chat?conv=${r.data.id}`, { replace: true })
      })
      .catch((e) => {
        setErrMsg((e as Error).message || '加入失败，邀请码可能已失效')
        setStatus('error')
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code])

  if (status === 'error') {
    return (
      <Result
        status="warning"
        title="加入群聊失败"
        subTitle={errMsg}
        extra={
          <Button type="primary" onClick={() => navigate('/group-chat', { replace: true })}>
            去群聊
          </Button>
        }
      />
    )
  }
  return (
    <div style={{ textAlign: 'center', paddingTop: 120 }}>
      <Spin size="large" tip="正在加入群聊…">
        <div style={{ height: 40 }} />
      </Spin>
    </div>
  )
}
