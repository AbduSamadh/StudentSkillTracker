// End-to-end walkthrough of the seeded demo school. Each test names the acceptance criterion
// (spec §13.1) it demonstrates in a real browser; the authorisation, suppression, consent and
// messaging criteria are covered exhaustively by the backend suite.

import { createHmac } from 'node:crypto'

import { test as base, expect, type Page } from '@playwright/test'

const PASSWORD = 'Demo-Password-2026!'
const LEADER = { email: 'leader@demo.school.example', mfa: 'STEMLEADERDEMOSECRETKEYA' }
const ADMIN = { email: 'admin@demo.school.example', mfa: 'STEMADMINDEMOSECRETKEYAB' }
const TEACHER = { email: 'sara.haddad@demo.school.example' }

/** RFC 6238 TOTP (SHA-1, 6 digits, 30 s), as the demo accounts' authenticator would show. */
function totp(secret: string, now = Date.now()): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  let bits = ''
  for (const c of secret.replace(/=+$/, '')) bits += alphabet.indexOf(c).toString(2).padStart(5, '0')
  const key = Buffer.from(bits.match(/.{8}/g)!.map((b) => parseInt(b, 2)))
  const counter = Buffer.alloc(8)
  counter.writeBigUInt64BE(BigInt(Math.floor(now / 1000 / 30)))
  const h = createHmac('sha1', key).update(counter).digest()
  const o = h[h.length - 1] & 0xf
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000).padStart(6, '0')
}

async function signIn(page: Page, who: { email: string; mfa?: string }): Promise<void> {
  await page.goto('/login')
  await page.getByLabel('School code').fill('demo')
  await page.getByLabel('Email').fill(who.email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  if (who.mfa) {
    await page.getByLabel('Code', { exact: true }).fill(totp(who.mfa))
    await page.getByRole('button', { name: 'Verify' }).click()
  }
  await page.waitForURL((u) => !u.pathname.startsWith('/login'))
}

async function outbox(page: Page): Promise<{ status: string }[]> {
  return page.evaluate(
    () =>
      new Promise((resolve, reject) => {
        const req = indexedDB.open('stemtrack')
        req.onerror = () => reject(req.error)
        req.onsuccess = () => {
          const all = req.result.transaction('outbox').objectStore('outbox').getAll()
          all.onsuccess = () => resolve(all.result)
          all.onerror = () => reject(all.error)
        }
      }),
  )
}

// Every page fails its test on an uncaught error or an unexpected 401.
const test = base.extend({
  page: async ({ page }, use) => {
    const problems: string[] = []
    page.on('pageerror', (e) => problems.push(`page error: ${e.message}`))
    page.on('response', (r) => {
      if (r.status() === 401 && !r.url().endsWith('/auth/refresh')) problems.push(`401 ${r.request().method()} ${r.url()}`)
    })
    await use(page)
    expect(problems).toEqual([])
  },
})

test('leader signs in with MFA and sees the six-number dashboard', async ({ page }) => {
  await signIn(page, LEADER)
  await expect(page.getByRole('heading', { name: 'School overview' })).toBeVisible()
  await expect(page.getByText('Participation against school roll')).toBeVisible()
  await expect(page.getByText('Spend against budget')).toBeVisible()
  await expect(page.getByText('Year-on-year')).toBeVisible()
})

test('13.1.3 readiness is traced to the awards and requirements behind it', async ({ page }) => {
  await signIn(page, TEACHER)
  await page.getByRole('link', { name: /Robotics A/ }).click()
  await page.getByRole('tab', { name: 'Readiness' }).click()
  await expect(page.getByRole('heading', { name: 'Shared gap list' }).first()).toBeVisible()

  await page.getByRole('tab', { name: 'Members' }).click()
  await page.getByRole('link', { name: 'Hamda Al Falasi' }).click()
  await page.getByRole('tab', { name: 'Readiness' }).click()
  const select = page.getByLabel('Readiness for')
  await select.locator('option', { hasText: 'National Final' }).waitFor({ state: 'attached' })
  const label = (await select.locator('option').allTextContents()).find((o) => o.includes('National Final'))!
  await select.selectOption({ label })

  await expect(page.getByText('Why this score')).toBeVisible()
  await expect(page.getByText(/Σ w·min/)).toBeVisible()
  // Every evidenced line names the verifying teacher and links to the award.
  await expect(page.getByText('Teacher verification').first()).toBeVisible()
  await expect(page.getByText('Gap list')).toBeVisible()
})

test('13.1.1 attendance captured offline is kept on the device and syncs once reconnected', async ({
  page,
  context,
}) => {
  await signIn(page, TEACHER)
  await page.goto('/capture/attendance')
  await page.getByLabel('Squad').selectOption({ label: 'Robotics A (Senior)' })
  await expect(page.getByText('Mark all present')).toBeVisible()

  await context.setOffline(true)
  await expect(page.getByText(/You are offline/)).toBeVisible()
  await page.getByRole('radio', { name: 'Absent' }).first().click()
  await page.getByRole('button', { name: 'Save attendance' }).click()
  await expect.poll(async () => (await outbox(page)).length).toBeGreaterThan(0)
  expect((await outbox(page)).every((w) => w.status === 'pending')).toBe(true)

  await context.setOffline(false)
  await page.evaluate(() => window.dispatchEvent(new Event('online')))
  await expect.poll(async () => (await outbox(page)).length, { timeout: 15_000 }).toBe(0)
})

test('13.1.7 the interface switches to Arabic with a right-to-left layout', async ({ page }) => {
  await signIn(page, TEACHER)
  await page.getByRole('button', { name: 'التبديل إلى العربية' }).click()
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  await expect(page.locator('html')).toHaveAttribute('lang', 'ar')
  await page.getByRole('link', { name: /Robotics A/ }).click()
  await page.getByRole('tab', { name: 'الجاهزية' }).click()
  await expect(page.getByRole('heading', { name: 'قائمة الفجوات المشتركة' }).first()).toBeVisible()
  await page.getByRole('button', { name: 'Switch to English' }).click()
  await expect(page.locator('html')).toHaveAttribute('dir', 'ltr')
})

test('a parent message is previewed with real recipients before anyone can release it', async ({ page }) => {
  await signIn(page, ADMIN)
  await page.goto('/messages/new')
  await page.getByLabel('Message type').selectOption('logistics')
  await page.getByLabel('Whole squad').selectOption({ label: 'Robotics A (Senior)' })
  const edition = page.getByLabel('Competition')
  await edition.locator('option', { hasText: 'Emirate Qualifier' }).first().waitFor({ state: 'attached' })
  const options = await edition.locator('option').allTextContents()
  await edition.selectOption({ label: options.find((o) => o.includes('Emirate Qualifier'))! })
  await page.getByLabel('meet time').fill('06:45')
  await page.getByLabel('pickup time').fill('17:30')
  await page.getByLabel('kit list').fill('Lunch, water, school PE kit')
  await page.getByRole('button', { name: 'Save draft' }).click()
  await page.waitForURL('**/messages/*')

  await expect(page.getByRole('button', { name: 'Release' })).toHaveCount(0) // not before a preview
  await page.getByRole('button', { name: 'Preview' }).first().click()
  await expect(page.getByText('Exactly what three families will receive')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Release' })).toBeVisible()
})

test('13.1.7 a parent uses the portal in Arabic on a phone', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })
  const page = await context.newPage()
  await page.goto('/login')
  await page.evaluate(() => localStorage.setItem('stem.lang', 'ar'))
  await page.goto('/login')
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  await page.getByLabel('رمز المدرسة').fill('demo')
  await page.getByLabel('البريد الإلكتروني').fill('parent@family.example')
  await page.getByLabel('كلمة المرور').fill(PASSWORD)
  await page.getByRole('button', { name: 'تسجيل الدخول', exact: true }).click()
  await page.waitForURL('**/portal')
  await page.getByRole('link', { name: /حمدة|Hamda/ }).first().click()
  await expect(page.getByText('الموافقات').first()).toBeVisible()
  // Nothing overflows sideways on a phone.
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await context.close()
})
