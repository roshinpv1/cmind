---
name: accelq-to-playwright
description: >
  Use this skill whenever a user wants to migrate, convert, translate, or port test scenarios,
  test cases, flows, or automation scripts from AccelQ to Playwright (TypeScript or JavaScript).
  Triggers on phrases like "migrate AccelQ", "convert AccelQ tests", "port AccelQ to Playwright",
  "AccelQ to Playwright", "export AccelQ scenarios", or any mention of both AccelQ and Playwright
  in the same context. Also use when a user wants to replicate AccelQ test coverage in a
  code-based framework. This skill covers the full end-to-end migration lifecycle — discovery,
  mapping, conversion, validation, CI setup, and reporting — to guarantee 100% scenario coverage
  and a zero-failure migration.
---

# AccelQ → Playwright Migration Playbook

## Overview

This skill governs the complete, agentic migration of AccelQ test scenarios into Playwright
(TypeScript-first, JavaScript fallback). It is structured as a **multi-phase pipeline** with
explicit gates between phases to guarantee correctness, traceability, and 100% success rate.

**Stack defaults (override on user instruction):**
- Playwright + TypeScript
- `@playwright/test` test runner
- Page Object Model (POM) architecture
- GitHub Actions (or user's preferred CI)

---

## Pre-Flight: Before You Write a Single Line of Code

### Step 0 — Gather Inputs

Ask the user for (or infer from context):

| Input | Why Needed |
|---|---|
| AccelQ export file(s) — JSON, XML, CSV, or screenshots | Source of truth for all scenarios |
| AccelQ project name / module list | Scoping the migration |
| Target environments (dev, staging, prod) | baseURL config |
| Auth mechanism (username/password, SSO, token, cookie) | Auth fixture design |
| Existing Playwright project? Yes/No | Determines scaffold vs. integrate |
| CI system (GitHub Actions, Azure DevOps, Jenkins, etc.) | Pipeline YAML choice |
| Browser targets (Chromium, Firefox, WebKit) | `playwright.config.ts` |
| Parallelism constraints | Worker/shard config |
| Reporting needs (Allure, HTML, Slack, JIRA) | Reporter plugins |

> **If AccelQ export is unavailable:** Ask the user to export via:
> AccelQ → Project → Export → Download as JSON/CSV. If export is blocked, request
> screenshots or a written description of each scenario, then reverse-engineer.

---

## Phase 1 — Discovery & Inventory

### 1.1 Parse AccelQ Scenarios

For each AccelQ scenario/test case, extract and record in a structured manifest:

```
SCENARIO_MANIFEST (internal tracking table)
────────────────────────────────────────────
ID          | Unique AccelQ scenario/case ID
Name        | Human-readable name
Module      | AccelQ module / folder
Steps       | Ordered list of actions + assertions
Test Data   | Inline data, data tables, or referenced datasets
Tags        | Smoke, Regression, P1, etc.
Dependencies| Pre-conditions, login state, setup scenarios
Status      | PENDING → IN_PROGRESS → CONVERTED → VALIDATED
Notes       | Edge cases, known AccelQ quirks
```

**AccelQ concept → Playwright mapping cheat sheet:**

| AccelQ Concept | Playwright Equivalent |
|---|---|
| Scenario / Test Case | `test('...', async ({ page }) => { })` |
| Step Group / Module | Helper function or Page Object method |
| Test Flow | `test.describe` block |
| Test Suite | `test.describe` with `beforeAll`/`afterAll` |
| Data-driven (inline) | `test.each([...])` |
| Data-driven (external CSV/Excel) | Load file in `beforeAll`, use `test.each` |
| Checkpoint / Assertion | `expect(locator).toHave*()` |
| Screenshot on Failure | Built-in; enabled via config |
| Email/SMS action | Mock via `page.route()` or API intercept |
| Wait / Pause step | `page.waitForSelector()` / `page.waitForResponse()` |
| Loop | `for...of` loop inside test |
| Conditional step | `if/else` with `page.isVisible()` |
| Reusable Component | Page Object class method |
| Environment variable | `.env` + `process.env` |
| Global Setup | `globalSetup` in `playwright.config.ts` |
| AccelQ Agent / Runner | Playwright workers |

### 1.2 Risk Classification

Classify every scenario into one of three tiers:

- **🟢 GREEN (Straightforward):** UI-only, single browser, no external deps → direct 1:1 conversion
- **🟡 YELLOW (Needs attention):** Has file uploads, iframes, multi-tab, drag-and-drop, or OAuth → requires special handling patterns
- **🔴 RED (Complex):** Involves native desktop popups, biometric auth, deeply embedded widgets, CAPTCHA, or AccelQ-proprietary integrations → needs design decision before coding

> Surface all RED items to the user immediately and agree on a strategy before proceeding.

---

## Phase 2 — Project Scaffold

### 2.1 Initialize Playwright Project

```bash
# If starting fresh:
npm init playwright@latest

# Recommended structure:
project-root/
├── playwright.config.ts
├── .env                      # environment secrets
├── .env.example              # committed template
├── global-setup.ts           # auth, DB seed, etc.
├── tests/
│   ├── [module-name]/        # mirrors AccelQ module structure
│   │   ├── [scenario-name].spec.ts
│   │   └── ...
├── pages/                    # Page Object classes
│   ├── BasePage.ts
│   └── [page-name].page.ts
├── fixtures/
│   ├── auth.fixture.ts       # login state storage
│   └── index.ts
├── helpers/
│   ├── api.helper.ts         # API shortcuts
│   └── data.helper.ts        # test data loaders
├── test-data/
│   ├── [scenario-id].json
│   └── ...
└── reports/                  # gitignored output
```

### 2.2 `playwright.config.ts` Template

```typescript
import { defineConfig, devices } from '@playwright/test';
import dotenv from 'dotenv';
dotenv.config();

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 4 : undefined,
  reporter: [
    ['html', { outputFolder: 'reports/html', open: 'never' }],
    ['junit', { outputFile: 'reports/junit.xml' }],
    ['list'],
  ],
  use: {
    baseURL: process.env.BASE_URL ?? 'http://localhost:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'on-first-retry',
    actionTimeout: 10_000,
    navigationTimeout: 30_000,
  },
  projects: [
    // Setup project (auth)
    { name: 'setup', testMatch: /global-setup\.ts/ },
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
      dependencies: ['setup'],
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
      dependencies: ['setup'],
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
      dependencies: ['setup'],
    },
  ],
});
```

---

## Phase 3 — Conversion (Scenario by Scenario)

Work through the SCENARIO_MANIFEST in this order: GREEN → YELLOW → RED.

### 3.1 Conversion Protocol (Per Scenario)

For **every** AccelQ scenario, follow these steps in order:

**Step A — Read the AccelQ scenario completely** before writing any code.

**Step B — Map each AccelQ step to a Playwright action** using the cheat sheet in §1.1.

**Step C — Identify the Page Object** responsible for each screen. Create or extend POM class.

**Step D — Write the `.spec.ts` file** following the template below.

**Step E — Add the scenario ID as a tag** for traceability.

**Step F — Update SCENARIO_MANIFEST** to IN_PROGRESS, then CONVERTED.

### 3.2 Spec File Template

```typescript
import { test, expect } from '../fixtures';  // use custom fixture
import { LoginPage } from '../pages/login.page';
import { DashboardPage } from '../pages/dashboard.page';

// AccelQ Scenario ID: [ACCELQ-SCENARIO-ID]
// AccelQ Module: [Module Name]
// Priority: [P1/P2/P3]
test.describe('[Module Name]', () => {

  test.beforeEach(async ({ page }) => {
    // Pre-conditions from AccelQ scenario
  });

  test('[Scenario Name] @accelq-[ID] @smoke', async ({ page }) => {
    const loginPage = new LoginPage(page);
    const dashboard = new DashboardPage(page);

    // Step 1: [AccelQ step description]
    await loginPage.goto();
    await loginPage.login(process.env.TEST_USER!, process.env.TEST_PASS!);

    // Step 2: [AccelQ step description]
    await expect(dashboard.welcomeMessage).toBeVisible();

    // Checkpoint [AccelQ assertion ID]:
    await expect(page).toHaveTitle(/Dashboard/);
  });

});
```

### 3.3 Page Object Template

```typescript
import { type Page, type Locator } from '@playwright/test';

export class ExamplePage {
  readonly page: Page;
  // Declare all locators as readonly class properties
  readonly submitButton: Locator;
  readonly errorMessage: Locator;

  constructor(page: Page) {
    this.page = page;
    // Prefer: role > label > placeholder > test-id > CSS (last resort)
    this.submitButton = page.getByRole('button', { name: 'Submit' });
    this.errorMessage = page.getByTestId('error-msg');
  }

  async goto() {
    await this.page.goto('/example');
  }

  async fillForm(data: { name: string; email: string }) {
    await this.page.getByLabel('Name').fill(data.name);
    await this.page.getByLabel('Email').fill(data.email);
  }
}
```

### 3.4 Special Handling Patterns (YELLOW scenarios)

**File Upload:**
```typescript
await page.getByLabel('Upload').setInputFiles('test-data/sample.pdf');
```

**iFrame:**
```typescript
const frame = page.frameLocator('#iframe-id');
await frame.getByRole('button', { name: 'Submit' }).click();
```

**Multi-tab / Popup:**
```typescript
const [popup] = await Promise.all([
  page.waitForEvent('popup'),
  page.getByText('Open new tab').click(),
]);
await popup.waitForLoadState();
```

**File Download:**
```typescript
const [download] = await Promise.all([
  page.waitForEvent('download'),
  page.getByText('Download').click(),
]);
await download.saveAs('./downloads/' + download.suggestedFilename());
```

**Drag and Drop:**
```typescript
await page.dragAndDrop('#source', '#target');
```

**API Interception / Mock:**
```typescript
await page.route('**/api/endpoint', route =>
  route.fulfill({ status: 200, body: JSON.stringify({ mocked: true }) })
);
```

**Date Picker (AccelQ often handles these via injection):**
```typescript
// Direct value injection is most reliable:
await page.evaluate(() => {
  const input = document.querySelector('#datepicker') as HTMLInputElement;
  input.value = '2025-12-31';
  input.dispatchEvent(new Event('change', { bubbles: true }));
});
```

**Soft Assertions (AccelQ "non-blocking checkpoints"):**
```typescript
// Gather all soft assertion failures, report at end
const softExpect = expect.soft;
await softExpect(page.getByTestId('price')).toContainText('$');
await softExpect(page.getByTestId('stock')).toBeVisible();
// Hard stop:
expect(softExpect).toPass(); // throws if any soft assertion failed
```

### 3.5 Data-Driven Scenarios

**Inline (AccelQ "parameterized steps"):**
```typescript
const testData = [
  { username: 'user1@test.com', role: 'admin' },
  { username: 'user2@test.com', role: 'viewer' },
];

for (const { username, role } of testData) {
  test(`Login as ${role}`, async ({ page }) => {
    // ...
  });
}
```

**External CSV/Excel (AccelQ data tables):**
```typescript
// helpers/data.helper.ts
import fs from 'fs';
import { parse } from 'csv-parse/sync';

export function loadCSV(filename: string) {
  return parse(fs.readFileSync(`test-data/${filename}`), {
    columns: true, skip_empty_lines: true
  });
}
```

### 3.6 Auth Fixture (Replaces AccelQ Login Reuse)

```typescript
// global-setup.ts
import { chromium } from '@playwright/test';

export default async function globalSetup() {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto(process.env.BASE_URL + '/login');
  await page.getByLabel('Email').fill(process.env.TEST_USER!);
  await page.getByLabel('Password').fill(process.env.TEST_PASS!);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.context().storageState({ path: '.auth/user.json' });
  await browser.close();
}

// playwright.config.ts → use: { storageState: '.auth/user.json' }
```

---

## Phase 4 — Validation

### 4.1 Per-Scenario Validation Checklist

After converting each scenario, verify:

- [ ] All AccelQ steps are represented in the Playwright test (no step silently dropped)
- [ ] All AccelQ checkpoints/assertions are present as `expect()` calls
- [ ] Test data matches the original AccelQ data sets
- [ ] Tags match (smoke, regression, priority)
- [ ] AccelQ scenario ID is in the test title or as a tag
- [ ] No hardcoded credentials or URLs (use `.env`)
- [ ] Test is deterministic (no `page.waitForTimeout()` without justification)

### 4.2 Execution Validation

Run the converted suite progressively:

```bash
# Run a single scenario first:
npx playwright test tests/[module]/[scenario].spec.ts --headed

# Run by tag:
npx playwright test --grep "@smoke"

# Run full suite:
npx playwright test

# Run with trace (for debugging failures):
npx playwright test --trace on
```

### 4.3 Migration Coverage Report

Generate and present a coverage matrix to the user:

```
MIGRATION COVERAGE MATRIX
══════════════════════════════════════════════════════
Module          | Total AccelQ | Converted | Pass | Fail | Skip
─────────────────────────────────────────────────────
Login           |     8        |     8     |   8  |   0  |   0
Dashboard       |    15        |    15     |  14  |   1  |   0
Checkout        |    22        |    22     |  22  |   0  |   0
─────────────────────────────────────────────────────
TOTAL           |    45        |    45     |  44  |   1  |   0
Coverage        |  100%        |   98%     |
══════════════════════════════════════════════════════
```

**100% success means:** Every AccelQ scenario is converted AND passes in at least one browser.
Any failure must be investigated and resolved before declaring migration complete.

### 4.4 Failure Triage Protocol

For every failing test:

1. Open the HTML report: `npx playwright show-report`
2. Check trace viewer for the exact failing step
3. Classify the failure:
   - **Locator issue** → update POM, use `npx playwright codegen` to re-inspect
   - **Timing issue** → replace `waitForTimeout` with `waitForSelector`/`waitForResponse`
   - **Environment issue** → verify `.env` values, check baseURL
   - **AccelQ mismatch** → re-read AccelQ scenario; the step may have been misunderstood
   - **Genuine bug in AUT** → log separately; this is not a migration failure
4. Fix, re-run, update SCENARIO_MANIFEST to VALIDATED.

---

## Phase 5 — CI/CD Integration

### 5.1 GitHub Actions Pipeline

```yaml
# .github/workflows/playwright.yml
name: Playwright Tests

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]
  schedule:
    - cron: '0 2 * * *'  # Nightly full suite

jobs:
  test:
    timeout-minutes: 60
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        shard: [1, 2, 3, 4]  # Parallel shards

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: 'npm'

      - run: npm ci

      - run: npx playwright install --with-deps

      - name: Run Playwright tests (shard ${{ matrix.shard }}/4)
        run: npx playwright test --shard=${{ matrix.shard }}/4
        env:
          BASE_URL: ${{ secrets.BASE_URL }}
          TEST_USER: ${{ secrets.TEST_USER }}
          TEST_PASS: ${{ secrets.TEST_PASS }}

      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: playwright-report-shard-${{ matrix.shard }}
          path: playwright-report/
          retention-days: 30

  merge-reports:
    needs: test
    if: always()
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm ci
      - uses: actions/download-artifact@v4
        with:
          pattern: playwright-report-shard-*
          merge-multiple: true
      - run: npx playwright merge-reports --reporter html .
      - uses: actions/upload-artifact@v4
        with:
          name: playwright-report-merged
          path: playwright-report/
```

### 5.2 Azure DevOps Pipeline (Alternative)

```yaml
trigger:
  branches:
    include: [main]

pool:
  vmImage: ubuntu-latest

steps:
  - task: NodeTool@0
    inputs:
      versionSpec: '20.x'

  - script: npm ci
  - script: npx playwright install --with-deps
  - script: npx playwright test
    env:
      BASE_URL: $(BASE_URL)
      TEST_USER: $(TEST_USER)
      TEST_PASS: $(TEST_PASS)

  - task: PublishTestResults@2
    condition: always()
    inputs:
      testResultsFormat: JUnit
      testResultsFiles: reports/junit.xml

  - task: PublishPipelineArtifact@1
    condition: always()
    inputs:
      targetPath: playwright-report
      artifact: playwright-report
```

---

## Phase 6 — Reporting & Handover

### 6.1 Migration Completion Report (deliver to user)

Produce a Markdown report covering:

```markdown
# AccelQ → Playwright Migration Report
**Date:** [date]
**Project:** [project name]

## Summary
- Total AccelQ scenarios inventoried: N
- Scenarios converted: N (100%)
- Scenarios passing: N
- Scenarios with known issues: N (list below)
- Browser coverage: Chromium ✅ Firefox ✅ WebKit ✅
- CI pipeline: ✅ Configured

## Scenario Coverage by Module
[COVERAGE MATRIX from §4.3]

## Architecture Decisions
- [Auth approach chosen and why]
- [POM structure rationale]
- [Data strategy rationale]

## Known Issues / Deferred Items
| Scenario ID | Issue | Recommendation |
|---|---|---|
| ACCELQ-123 | CAPTCHA present | Use test env with CAPTCHA disabled |

## How to Run
```bash
# All tests
npx playwright test

# Smoke only
npx playwright test --grep "@smoke"

# Headed (debug)
npx playwright test --headed

# With trace
npx playwright test --trace on; npx playwright show-report
```

## File Structure
[Paste final directory tree]
```

---

## Quality Gates (Must Pass Before Declaring Done)

| Gate | Criterion | Check |
|---|---|---|
| G1 | 100% AccelQ scenarios in SCENARIO_MANIFEST | Manual audit |
| G2 | 100% scenarios in CONVERTED status | SCENARIO_MANIFEST |
| G3 | 100% scenarios in VALIDATED status | SCENARIO_MANIFEST |
| G4 | 0 unresolved test failures from migration issues | CI green |
| G5 | All tests tagged with AccelQ scenario ID | `grep @accelq` |
| G6 | No hardcoded secrets | `grep -r 'password'` |
| G7 | CI pipeline passes end-to-end | CI green |
| G8 | Migration report delivered | Report file |

---

## Common AccelQ Anti-Patterns & Their Playwright Fixes

| AccelQ Pattern | Problem | Playwright Fix |
|---|---|---|
| `sleep(3000)` wait steps | Flaky, slow | `waitForSelector` / `waitForLoadState` |
| XPath locators from AccelQ recording | Brittle | Rewrite as `getByRole`/`getByLabel` |
| Hardcoded test user in AccelQ config | Security | `.env` + CI secrets |
| Sequential-only execution | Slow | `fullyParallel: true` in config |
| Re-login on every test | Slow | `storageState` auth fixture |
| Ignoring browser console errors | Bugs slip through | `page.on('console', ...)` listener |
| No assertions, only "click through" | Low value tests | Add at least one `expect()` per scenario |

---

## Locator Strategy Priority (follow this order strictly)

1. `page.getByRole()` — semantic, most stable
2. `page.getByLabel()` — form fields
3. `page.getByPlaceholder()` — inputs
4. `page.getByText()` — static text
5. `page.getByTestId()` — `data-testid` attributes (request devs to add if missing)
6. `page.locator('css')` — fallback for complex selectors
7. **Never** use auto-generated AccelQ XPath selectors verbatim

---

## References

- [Playwright Docs](https://playwright.dev/docs/intro)
- [Page Object Model](https://playwright.dev/docs/pom)
- [Test Fixtures](https://playwright.dev/docs/test-fixtures)
- [Auth](https://playwright.dev/docs/auth)
- [CI](https://playwright.dev/docs/ci-intro)
- [Trace Viewer](https://playwright.dev/docs/trace-viewer)
- [API Testing](https://playwright.dev/docs/api-testing)
- [Visual Comparisons](https://playwright.dev/docs/test-snapshots)
