const header = document.querySelector('[data-header]')
const menuButton = document.querySelector('.menu-button')
const mobileMenu = document.querySelector('.mobile-menu')
const navLinks = [...document.querySelectorAll('.desktop-nav a')]
const revealItems = [...document.querySelectorAll('.reveal')]
const glow = document.querySelector('.cursor-glow')
const toast = document.querySelector('.toast')

const updateHeader = () => header?.classList.toggle('scrolled', window.scrollY > 24)
updateHeader()
window.addEventListener('scroll', updateHeader, { passive: true })

const closeMenu = () => {
  menuButton?.setAttribute('aria-expanded', 'false')
  menuButton?.setAttribute('aria-label', '打开菜单')
  mobileMenu?.classList.remove('open')
  mobileMenu?.setAttribute('aria-hidden', 'true')
  document.body.style.overflow = ''
}

menuButton?.addEventListener('click', () => {
  const open = menuButton.getAttribute('aria-expanded') !== 'true'
  menuButton.setAttribute('aria-expanded', String(open))
  menuButton.setAttribute('aria-label', open ? '关闭菜单' : '打开菜单')
  mobileMenu?.classList.toggle('open', open)
  mobileMenu?.setAttribute('aria-hidden', String(!open))
  document.body.style.overflow = open ? 'hidden' : ''
})

mobileMenu?.querySelectorAll('a').forEach((link) => link.addEventListener('click', closeMenu))

const revealObserver = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return
      const item = entry.target
      const delay = Number(item.dataset.delay || 0)
      window.setTimeout(() => item.classList.add('visible'), delay)
      revealObserver.unobserve(item)
    })
  },
  { threshold: 0.12 },
)
revealItems.forEach((item) => revealObserver.observe(item))

const sectionObserver = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return
      navLinks.forEach((link) => link.classList.toggle('active', link.hash === `#${entry.target.id}`))
    })
  },
  { rootMargin: '-35% 0px -55% 0px' },
)
document.querySelectorAll('main section[id]').forEach((section) => sectionObserver.observe(section))

window.addEventListener(
  'pointermove',
  (event) => {
    if (!glow) return
    glow.style.left = `${event.clientX}px`
    glow.style.top = `${event.clientY}px`
  },
  { passive: true },
)

document.querySelector('.copy-command')?.addEventListener('click', async (event) => {
  const button = event.currentTarget
  const value = button.dataset.copy
  if (!value) return
  try {
    await navigator.clipboard.writeText(value)
    button.querySelector('span').textContent = '已复制'
    toast?.classList.add('show')
    window.setTimeout(() => {
      button.querySelector('span').textContent = '复制克隆命令'
      toast?.classList.remove('show')
    }, 1800)
  } catch {
    window.prompt('复制克隆命令：', value)
  }
})
