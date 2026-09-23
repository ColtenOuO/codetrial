// A browser-level pin for the bracket auto-close pipeline in web/editor.js
// and web/interview.js.
import { after, before, test } from "node:test";
import assert from "node:assert/strict";

import { DEFAULT_RUNTIME_CONFIG, launchChromium, startStaticServer } from "./source.js";

let browser = null;
let server = null;
let base = "";

before(async () => {
  browser = await launchChromium();
  if (!browser) return;
  ({ server, base } = await startStaticServer({ runtimeConfig: DEFAULT_RUNTIME_CONFIG }));
});

after(async () => {
  await browser?.close();
  await new Promise((closed) => (server ? server.close(closed) : closed()));
});

/// A page on the interview screen with an empty editor, no room joined. The
/// editor is enabled as soon as bindEvents runs; nothing under test needs a
/// live session, so this never waits on one.
async function editorPage(t) {
  if (!browser) {
    t.skip("playwright chromium unavailable");
    return null;
  }
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${base}/interview.html?problem=chargeback-pair-match`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#editor:not([disabled])");
  // The media preflight overlay sits over the editor until a candidate (or a
  // stubbed device) clears it, and it blocks Playwright's own click on
  // whatever is under it. A keystroke only needs the element focused, not
  // clicked, so this reaches through the overlay the same way the existing
  // candidate-cases.test.js does for its own buttons -- .click()/.focus() in
  // page.evaluate bypasses the hit-test a real pointer click would fail.
  await page.evaluate(() => document.querySelector("#editor").focus());
  // The starter code carries its own brackets; clearing it keeps every
  // assertion below about exactly the characters this test types.
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.press("Delete");
  await page.waitForFunction(() => document.querySelector("#editor").value === "");
  return { page, errors };
}

/// The editor's value and caret, the way lobby.test.js's snapshot(page) reads
/// the lobby: one shape, so the two tests that read it assert against the
/// same fields instead of two literals that could quietly drift apart.
const caretState = (page) =>
  page.evaluate(() => {
    const editor = document.querySelector("#editor");
    return { value: editor.value, start: editor.selectionStart, end: editor.selectionEnd };
  });

test("typing an opening bracket auto-closes it, in a real browser", async (t) => {
  const ctx = await editorPage(t);
  if (!ctx) return;
  const { page, errors } = ctx;
  try {
    await page.keyboard.type("(");
    assert.deepEqual(await caretState(page), { value: "()", start: 1, end: 1 });
    assert.deepEqual(errors, [], `typing "(" threw in the browser: ${errors[0]}`);
  } finally {
    await page.close();
  }
});

test("closing a pair by hand types over the closer instead of doubling it, in a real browser", async (t) => {
  const ctx = await editorPage(t);
  if (!ctx) return;
  const { page, errors } = ctx;
  try {
    await page.keyboard.type("(");
    await page.keyboard.type("value");
    await page.keyboard.type(")");
    assert.deepEqual(await caretState(page), { value: "(value)", start: 7, end: 7 });
    assert.deepEqual(errors, [], `closing the pair threw in the browser: ${errors[0]}`);
  } finally {
    await page.close();
  }
});

test("Backspace right after an auto-closed pair removes both characters, in a real browser", async (t) => {
  const ctx = await editorPage(t);
  if (!ctx) return;
  const { page, errors } = ctx;
  try {
    // deleteEmptyPair changes the value, so this exercises the other
    // execCommand round trip (execCommand("delete")), not just the
    // insertText one the test above covers.
    await page.keyboard.type("(");
    await page.keyboard.press("Backspace");
    assert.equal(await page.locator("#editor").inputValue(), "");
    assert.deepEqual(errors, [], `Backspace on an empty pair threw in the browser: ${errors[0]}`);
  } finally {
    await page.close();
  }
});
