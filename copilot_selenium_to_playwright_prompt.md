# VSCode Copilot Prompt: Selenium to Playwright Migration

Copy and paste the prompt below into VSCode Copilot Chat (or GitHub Copilot, ChatGPT, Claude, etc.) when you want to migrate a Selenium test to Playwright. 

**Instructions for you:**
Before sending this to Copilot, replace `[TARGET_LANGUAGE]` with your desired output language (e.g., **TypeScript**, **Python**, **Java**, or **C#**).

***

You are a **Principal Test Automation Architect**. Your task is to migrate the selected legacy Selenium WebDriver test file to a modern Playwright framework using **[TARGET_LANGUAGE]**.

Please rewrite the entire test file using Playwright's best practices. Apply the following rules and mindset shifts strictly:

### 1. Language-Specific Framework Integration
* **TypeScript/JavaScript**: Use `@playwright/test` runner. Remove `setUp`/`tearDown` and use built-in fixtures or `test.beforeEach`. Expect fully `await`-ed async/await syntax.
* **Python**: Use `pytest-playwright`. Convert classes to standard `pytest` functions with `page` fixtures. Use `playwright.sync_api` (synchronous) to keep test flows clean, unless async is explicitly requested.
* **Java**: Use the official `playwright-java` library with JUnit 5 or TestNG. Manage the `Playwright`, `Browser`, `BrowserContext`, and `Page` objects efficiently (preferably using a base test class or `@BeforeAll` / `@BeforeEach` annotations).
* **C# (.NET)**: Use `Microsoft.Playwright.NUnit` or `MSTest` base classes like `PageTest`. Do not manually manage `Playwright.CreateAsync()` if using the runner's base classes.

### 2. Setup & Browser Management
* Replace `WebDriver driver = new ChromeDriver()` (or equivalent) with Playwright's context/page initialization for the selected language.
* Remove `driver.manage().window().maximize()` and rely on Playwright's viewport configuration logic.
* **Never use WebDriverManager.** Playwright manages its own binaries.

### 3. Locators & Selectors
* Replace ID/XPath/CSS (`driver.findElement(By.id("x"))`) with semantic, accessibility-first locators where possible: `getByRole`, `getByLabel`, `getByText`, or `getByTestId`.
* **Playwright locators are lazy.** Remove any `try/catch` blocks used to check for element existence (`NoSuchElementException`). Instead, assert visibility: e.g., `expect(locator).not.toBeVisible()` (TS), `expect(locator).not_to_be_visible()` (Python), or `assertThat(locator).not().isVisible()` (Java).

### 4. Actions & Interactions
* Replace text input methods (`sendKeys()`) with `fill("text")`. Note that `fill` clears the field first. To append sequentially, use the equivalent of `pressSequentially("text")`.
* Replace `element.click()` with Playwright's native `locator.click()`.
* Replace standard `Select` wrappers with Playwright's native `selectOption()`.
* Replace robust `Actions` builder chains (Hover, DragAndDrop) with direct locator commands (e.g., `hover()`, `dragTo()`).

### 5. Waits & Timeouts (CRITICAL)
* **Delete all explicit waits** (`WebDriverWait`, `ExpectedConditions`). Playwright auto-waits for elements to be actionable before performing clicks, fills, etc.
* **Delete all implicit timeouts** (`driver.manage().timeouts().implicitlyWait`).
* **Delete all hardcoded sleeps** (`Thread.sleep`, `time.sleep`).
* Replace wait conditions with Web-First Assertions. Make sure the assertions are properly awaited or called synchronously based on the target language paradigms.

### 6. Advanced Features
* **Frames**: Replace `switchTo().frame()` with `frameLocator()`. Do not switch back to "default content"; standard page locators will continue to query the main page.
* **Windows/Tabs**: Handle new popup windows/tabs via event listeners (e.g., `waitForEvent('popup')` in TS, `expect_popup()` with context manager in Python, `waitForPopup()` in Java). Do not iterate over window handles.
* **Alerts**: Handle alerts/dialogs by registering a listener BEFORE the action that triggers the dialog (e.g., `page.onDialog()`).

### Output Requirements
* Output the complete, executable, 100% working code in **[TARGET_LANGUAGE]**.
* Ensure the code is clean, idiomatic to the selected language ecosystem, and relies on no global driver state.
* Define correct imports for the selected language.
* If you identify any anti-patterns in the original code (e.g., nested fragile XPaths, shared state loops), add a brief inline code comment explaining the Playwright improvement.

**Review the selected context/code and perform the complete migration.**
