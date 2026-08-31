/**
 * Drives a real recovery in a real browser and photographs every screen.
 *
 * The screenshots in the README are the only claim a reader can check without
 * installing anything, so they are taken from a live run against a live API
 * rather than mocked up: what is in the images is what the software did.
 *
 *   node tools/capture.mjs [baseUrl] [outDir]
 */

import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";

const BASE = process.argv[2] ?? "http://127.0.0.1:3001";
const OUT = process.argv[3] ?? "../docs/screens";

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function shoot(page, name, options = {}) {
  await page.screenshot({ path: `${OUT}/${name}.png`, ...options });
  process.stdout.write(`  ${name}.png\n`);
}

/** Put a section at the top of the viewport and let motion settle. */
async function focus(page, text, offset = 90) {
  const target = page.locator(`text=${text}`).first();
  await target.evaluate((node, top) => {
    const y = node.getBoundingClientRect().top + window.scrollY - top;
    window.scrollTo({ top: y, behavior: "instant" });
  }, offset);
  await wait(700);
}

const main = async () => {
  await mkdir(OUT, { recursive: true });

  // Honour a preinstalled Chromium when the environment pins one, so a CI image
  // or sandbox that ships its own browser is not made to download another.
  const browser = await chromium.launch(
    process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
  );
  const page = await browser.newPage({
    viewport: { width: 1600, height: 1000 },
    deviceScaleFactor: 1.5,
  });

  console.log("landing");
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  // Let the sector field sweep across once so the frame has recovery flares in
  // it, and park the cursor where its light reads against the headline.
  await page.mouse.move(1180, 560);
  await wait(2600);
  await shoot(page, "01-landing");

  await focus(page, "Five agents, in the only order", 140);
  await shoot(page, "02-pipeline");

  await focus(page, "why trust the answer", 140);
  await shoot(page, "03-evidence");

  await focus(page, "measured, not asserted", 140);
  await shoot(page, "04-coverage");

  console.log("run");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.getByRole("button", { name: /Recover the sample card/i }).click();
  await page.waitForSelector("text=Recovering", { timeout: 30_000 });
  await wait(450);
  await shoot(page, "05-running");

  await page.waitForSelector("text=Checked against what was actually done", { timeout: 180_000 });
  await wait(1400);

  console.log("results");
  await page.evaluate(() => window.scrollTo(0, 0));
  await wait(500);
  await shoot(page, "06-results-top");

  await focus(page, "Checked against what was actually done");
  await shoot(page, "07-verification");

  await focus(page, "Map of the card");
  await shoot(page, "08-map");

  // Pick a damaged file so the evidence panel shows failed checks, not just
  // ticks — the failures are the part that proves the verdicts mean something.
  const filesPanel = page.locator("div.panel", { has: page.getByPlaceholder(/Filter by name/i) });
  const damaged = filesPanel.locator("tbody tr", { hasText: "Partly damaged" }).first();
  if (await damaged.count()) {
    await damaged.scrollIntoViewIfNeeded();
    await damaged.click();
    await wait(700);
  }
  await focus(page, "Why this verdict", 130);
  await shoot(page, "09-files-and-evidence");

  await page.getByPlaceholder(/Ask/i).first().fill("which photos came back complete?");
  await page.keyboard.press("Enter");
  await wait(3200);
  await focus(page, "Ask about the recovered files", 640);
  await shoot(page, "10-ask");

  await page.evaluate(() => window.scrollTo(0, 0));
  await wait(400);
  await shoot(page, "11-full-results", { fullPage: true });

  await browser.close();
  console.log("done");
};

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
