// Takes screenshots of the portal in demo mode. Usage: node screenshots.cjs <base-url> <output-dir>
const { chromium } = require("playwright");
const fs = require("fs");

const [base = "http://127.0.0.1:8000", out = "shots"] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });
  const errors = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));

  async function signIn(name) {
    await page.goto(base);
    await page.getByRole("button", { name: new RegExp(name) }).click();
    await page.waitForSelector(".ff-shell");
  }
  async function shot(file) {
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${out}/${file}.png`, fullPage: false });
  }
  async function go(label, file) {
    await page.getByRole("link", { name: label, exact: true }).click();
    await page.waitForLoadState("networkidle");
    await shot(file);
  }

  await page.goto(base);
  await page.waitForSelector(".ff-people");
  await shot("00-signin");

  await signIn("Alice");
  await page.waitForLoadState("networkidle");
  await shot("01-fleet");
  await go("Operations", "02-operations");
  await go("Cost", "03-cost");
  await page.getByRole("tab", { name: "Cost Control" }).click();
  await page.waitForLoadState("networkidle");
  await shot("04-cost-controls");
  await page.getByRole("button", { name: "Create" }).first().click();
  await shot("05-cost-control-form");
  await page.keyboard.press("Escape");
  await go("Project monitor", "06-project-monitor");
  await page.getByLabel("Project").selectOption("fraud-risk");
  await page.waitForLoadState("networkidle");
  await shot("07-project-fraud-risk");
  await go("Cost centres", "08-cost-centres");
  await go("Notifications", "09-notifications");
  await go("Budgets & tags", "10-budgets");
  await go("Admin", "11-admin");
  await page.getByRole("tab", { name: "Audit log" }).click();
  await shot("12-audit");
  await go("Access help", "13-access");

  await page.selectOption('select[aria-label="Theme"]', "dark");
  await go("Fleet", "14-fleet-dark");

  // A person limited to one project
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn("Bob");
  await page.waitForLoadState("networkidle");
  await shot("15-bob-fleet");
  await page.getByRole("link", { name: "Cost", exact: true }).click();
  await page.waitForLoadState("networkidle");
  await shot("16-bob-cost");

  // Someone who isn't added
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.getByRole("button", { name: /Dave/ }).click();
  await page.waitForTimeout(800);
  await shot("17-not-added");

  console.log(errors.length ? "console errors:\n" + errors.join("\n") : "no console errors");
  await browser.close();
})();
