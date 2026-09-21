import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { existsSync, readFileSync, statSync } from "node:fs";
import { extname, join } from "node:path";

import { launchChromium, root } from "./source.js";

const web = join(root, "web");
let browser = null;
let server = null;
let base = "";

// Grown once per language the interview page pulls in; a table says which
// without a fifth nested conditional.
const contentTypes = {
  ".js": "text/javascript",
  ".html": "text/html",
  ".css": "text/css",
  ".wasm": "application/wasm",
};

before(async () => {
  browser = await launchChromium();
  if (!browser) return;
  server = createServer((request, response) => {
    const path = new URL(request.url, "http://candidate.invalid").pathname;
    // Served by the Rust server in the real app, so there is no file for it
    // here. `runtime_config_handler` in src/web/assets.rs is what this stands
    // in for, and tests/web.rs pins the real one.
    if (path === "/runtime-config.js") {
      response.setHeader("content-type", "text/javascript");
      response.end(`globalThis.CODETRIAL_COMPILER_EXPLORER_ENABLED = false;
globalThis.CODETRIAL_COMPILER_EXPLORER_BASE_URL = "";
globalThis.CODETRIAL_RECORDING_ENABLED = false;
globalThis.CODETRIAL_CONSENT_VERSION = "";
globalThis.CODETRIAL_REPLAY_VERSION = 1;
`);
      return;
    }
    const file = join(web, path === "/" ? "index.html" : path);
    if (!file.startsWith(web) || !existsSync(file) || statSync(file).isDirectory()) {
      response.statusCode = 404;
      response.end("not found");
      return;
    }
    response.setHeader("content-type", contentTypes[extname(file)] ?? "application/json");
    response.end(readFileSync(file));
  });
  await new Promise((listening) => server.listen(0, "127.0.0.1", listening));
  base = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  await browser?.close();
  await new Promise((closed) => (server ? server.close(closed) : closed()));
});

/// The interview page binds its handlers and fetches the judge after the
/// document arrives, and `addCandidateCase` awaits that judge before it accepts
/// anything. A click sent before either is a click nothing is listening for, so
/// the case never lands and the wait for it times out on a cold runner while
/// passing on a warm laptop. The placeholder is written from the judge's own
/// first case, so it appearing means both halves are ready.
async function candidateCasesReady(page) {
  await page.waitForFunction(
    () => (document.querySelector("#candidate-case-input")?.placeholder ?? "") !== "",
  );
}

async function runCandidate(language, code) {
  const page = await browser.newPage();
  try {
    await page.goto(base);
    return await page.evaluate(async ({ language, code }) => {
      const { runBrowserTests } = await import("/runners.js");
      return runBrowserTests("chargeback-pair-match", code, language, null, [{ input: [[2, 7, 11, 15], 9] }]);
    }, { language, code });
  } finally {
    await page.close();
  }
}

test("candidate case output is shown by the JavaScript runner", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const summary = await runCandidate("javascript", "function matchDisputedCharge(nums, target) { return [0, 1]; }");
  assert.equal(summary.total, summary.cases.length - 1);
  assert.equal(summary.cases.at(-1).candidate, true);
  assert.equal(summary.cases.at(-1).pass, null);
  assert.equal(summary.cases.at(-1).got, "[0,1]");
});

test("candidate case output is shown by the Python runner", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const summary = await runCandidate("python", "def matchDisputedCharge(nums, target):\n    return [0, 1]\n");
  assert.equal(summary.total, summary.cases.length - 1);
  assert.equal(summary.cases.at(-1).candidate, true);
  assert.equal(summary.cases.at(-1).pass, null);
  assert.equal(summary.cases.at(-1).got, "[0,1]");
});

test("candidate cases run when session storage cannot be written", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    await page.addInitScript(() => {
      const setItem = Storage.prototype.setItem;
      Storage.prototype.setItem = function store(key, value) {
        if (key.startsWith("codetrial.candidateCases.")) throw new DOMException("quota", "QuotaExceededError");
        return setItem.call(this, key, value);
      };
    });
    await page.goto(`${base}/interview.html?problem=chargeback-pair-match`, { waitUntil: "domcontentloaded" });
    await candidateCasesReady(page);
    await page.evaluate(() => {
      document.querySelector("#candidate-case-input").value = "[[2,7,11,15],9]";
      document.querySelector("#candidate-case-add").click();
    });
    await page.waitForFunction(() => document.querySelector("#candidate-case-list").children.length === 1);
    assert.equal(await page.locator("#candidate-case-input").inputValue(), "");

    await page.evaluate(() => {
      document.querySelector('[data-language="javascript"]').click();
      document.querySelector("#run-tests").click();
    });
    await page.waitForFunction(() => document.querySelector("#results-body").textContent.includes("Your case 1"));
  } finally {
    await page.close();
  }
});

// Blocked site data fails a step earlier than a refused write: reading the
// `sessionStorage` global itself throws. Resolving it at the call site put
// that throw after the case was pushed, so the run stopped over a valid case
// and the next attempt added it a second time.
test("candidate cases run when session storage cannot be reached", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    await page.addInitScript(() => {
      Object.defineProperty(window, "sessionStorage", {
        get() {
          throw new DOMException("blocked", "SecurityError");
        },
      });
    });
    await page.goto(`${base}/interview.html?problem=chargeback-pair-match`, { waitUntil: "domcontentloaded" });
    await candidateCasesReady(page);
    await page.evaluate(() => {
      document.querySelector("#candidate-case-input").value = "[[2,7,11,15],9]";
      document.querySelector('[data-language="javascript"]').click();
      document.querySelector("#run-tests").click();
    });
    await page.waitForFunction(() => document.querySelector("#results-body").textContent.includes("Your case 1"));
    assert.equal(await page.locator("#candidate-case-list").evaluate((list) => list.children.length), 1);
  } finally {
    await page.close();
  }
});

// The list length alone proves nothing here: the handler clears the input on
// success, so the later clicks would fail to parse an empty string and leave
// one case behind whether or not the button was disabled. What the guard
// decides is the status line, which those refusals would otherwise overwrite
// with a parse error.
test("rapid add clicks add one case and keep its status", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    await page.goto(`${base}/interview.html?problem=chargeback-pair-match`, { waitUntil: "domcontentloaded" });
    await candidateCasesReady(page);
    await page.evaluate(() => {
      const input = document.querySelector("#candidate-case-input");
      const add = document.querySelector("#candidate-case-add");
      for (let index = 0; index < 6; index++) {
        input.value = `[[${index},7,11,15],${index + 7}]`;
        add.click();
      }
    });
    await page.waitForFunction(() => document.querySelector("#candidate-case-list").children.length === 1);
    assert.equal(await page.locator("#candidate-case-list").locator("li").count(), 1);
    assert.equal(await page.locator("#candidate-case-status").textContent(), "1/5 cases ready.");
    // The addition reads the box when the judge answers, not when the button
    // was pressed, which is the same contract `runTests` relies on to pick up
    // whatever the candidate has typed. So the case that lands is the last
    // value written, not the first.
    assert.equal(
      await page.locator("#candidate-case-list").locator("li").textContent(),
      "Your case 1: [[5,7,11,15],12]",
    );
  } finally {
    await page.close();
  }
});

test("adding then running during judge load shares one addition", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    let releaseJudge;
    const judgeHeld = new Promise((resolve) => { releaseJudge = resolve; });
    await page.route("**/judges/chargeback-pair-match.json", async (route) => {
      await judgeHeld;
      await route.continue();
    });
    await page.goto(`${base}/interview.html?problem=chargeback-pair-match`, { waitUntil: "domcontentloaded" });
    // `candidateCasesReady` is no use here: it waits on the judge, which this
    // test is holding. Retry the click instead and let the guard report its
    // own readiness, because `addCandidateCase` disables the button before its
    // first await, so a click that took is proof `bindEvents` has run. That
    // function is synchronous, so the language tabs and the run button are
    // bound by then too.
    await page.waitForFunction(() => {
      const add = document.querySelector("#candidate-case-add");
      if (!add || add.disabled) return Boolean(add?.disabled);
      document.querySelector("#candidate-case-input").value = "[[2,7,11,15],9]";
      add.click();
      return add.disabled;
    });
    await page.evaluate(() => {
      document.querySelector('[data-language="javascript"]').click();
      document.querySelector("#run-tests").click();
    });
    releaseJudge();
    await page.waitForFunction(() => document.querySelector("#results-body").textContent.includes("Your case 1"));
    assert.equal(await page.locator("#candidate-case-status").textContent(), "1/5 cases ready.");
    assert.equal(await page.locator("#candidate-case-list").locator("li").count(), 1);
  } finally {
    await page.close();
  }
});

test("code that never runs is reported, not swallowed", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    await page.goto(base);
    // The candidate's code is the worker's own first statements now rather
    // than a string evaluated inside it, which is what lets the page withhold
    // `unsafe-eval`. It also moves where a failure to run surfaces: a syntax
    // error stops the worker script instead of throwing out of an eval, so it
    // arrives through `onerror`. Both still have to reach the candidate as the
    // reason their run produced nothing.
    const results = await page.evaluate(async () => {
      const { runBrowserTests } = await import("/runners.js");
      const run = (code) => runBrowserTests("chargeback-pair-match", code, "javascript");
      const [broken, throwing, missing] = await Promise.all([
        run("function matchDisputedCharge( {{{"),
        run("throw new Error('boom at top level');"),
        run("const notTheEntry = 1;"),
      ]);
      return [broken.setupError, throwing.setupError, missing.setupError];
    });
    assert.match(results[0], /SyntaxError/);
    assert.match(results[1], /boom at top level/);
    assert.match(results[2], /Could not find matchDisputedCharge/);
  } finally {
    await page.close();
  }
});

test("a malformed candidate case stops the run it was typed into", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    await page.goto(`${base}/interview.html?problem=chargeback-pair-match`, { waitUntil: "domcontentloaded" });
    await candidateCasesReady(page);
    // The opposite of the cap below. A case the cap turned away still lets the
    // run go ahead, and a case that cannot be parsed must not: running without
    // it answers a question the candidate did not ask, and the box still holds
    // what they typed. The reason is asserted to be the one the add path
    // wrote, because `runTests` used to recover it by reading the status line
    // back out, so rewording that line changed which runs were blocked.
    await page.evaluate(() => {
      document.querySelector("#candidate-case-input").value = "not json";
      document.querySelector('[data-language="javascript"]').click();
      document.querySelector("#run-tests").click();
    });
    await page.waitForFunction(() => {
      const results = document.querySelector("#results-body").textContent;
      return results.length > 0 && results === document.querySelector("#candidate-case-status").textContent;
    });
    assert.equal(await page.locator("#candidate-case-list").locator("li").count(), 0);
    assert.doesNotMatch(await page.locator("#results-body").textContent(), /test cases passed/);
    assert.equal(await page.locator("#run-tests").textContent(), "Run tests");
  } finally {
    await page.close();
  }
});

test("a sixth candidate case is refused without blocking the run", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    await page.goto(`${base}/interview.html?problem=chargeback-pair-match`, { waitUntil: "domcontentloaded" });
    await candidateCasesReady(page);
    // Fill the allowance, then type one more and run. The sixth is refused on
    // its own; the judge's cases and the five already accepted still run.
    //
    // Each add waits for the row it creates rather than for a fixed delay:
    // `addCandidateCase` awaits the judge before it accepts anything, so a
    // sleep long enough on a warm laptop is short enough on a cold runner to
    // let the next value overwrite the box mid-add and lose the case.
    for (let index = 0; index < 5; index++) {
      await page.evaluate((value) => {
        document.querySelector("#candidate-case-input").value = value;
        document.querySelector("#candidate-case-add").click();
      }, `[[${index},7,11,15],${index + 7}]`);
      await page.waitForFunction(
        (expected) => document.querySelector("#candidate-case-list").children.length === expected,
        index + 1,
      );
    }

    await page.evaluate(() => {
      document.querySelector("#candidate-case-input").value = "[[9,9,9,9],18]";
      document.querySelector('[data-language="javascript"]').click();
      document.querySelector("#run-tests").click();
    });
    await page.waitForFunction(() => document.querySelector("#results-body").textContent.includes("Your case 5"));
    assert.match(await page.locator("#candidate-case-status").textContent(), /up to 5 cases/);
    assert.equal(await page.locator("#candidate-case-list").locator("li").count(), 5);
  } finally {
    await page.close();
  }
});
